"""Rules on entities (track 5): what a rule is about and who imposes it, and rule_sources moved onto entities.

Built on 20261108020258_directory_tables (entities, entity_links, entity_values, the organization kind). This migration:

- org_rules gains source_entity_id (who imposes the rule: an organization entity, or self for our own rules),
  document_entity_id (the law, contract or terms it comes from), toward_scope (a JSON list of what the rule is about;
  backend/entities/rules.py reads it), applies_when (a condition on self, as text) and party_side (our_side or
  their_side, for a rule that binds a party). source_id is dropped once its values have moved.
- Every rule_sources row becomes an organization entity. A name that no live organization carries becomes a new one:
  kind government for a government source, company for a vendor, customer or other source, and properties.rule_source
  keeps the row's id, kind, creator and creation time. A name that one live organization already carries reuses that
  entity, with the same provenance written into its properties; its visibility does not change. A new entity gets an
  entity_sources row (rule_source, company), since rule sources were visible to the whole company.
- When self exists, a vendor links to self (vendor_to), a customer links to self (customer_of) and a government governs
  self. Those links rest on the source (source_type rule_source).
- org_rules.source_entity_id and rule_reads.source_id take the new entity ids. Then rule_sources is dropped.

It refuses rather than guesses, and a raise rolls the whole migration back, so startup stops until the data is fixed
by hand: a name that is the company itself (self), a name that more than one live organization carries, a name whose
one match has a kind the rule source's kind does not accept, a second rule source for an entity that already took one,
and an org_rules row whose source_id matches no rule source.

Track 5 changes the code that reads these tables in the same change: org_rules.py, counsel.py and the Directory store.
"""

import json
import re
import uuid
from datetime import datetime, timezone

FOREIGN_KEYS_OFF = False  # columns are added and dropped, and rule_sources is dropped whole; nothing is rebuilt

# The organization kind a rule source's kind becomes when it has no entity yet.
KIND_OF = {"vendor": "company", "customer": "company", "government": "government", "other": "company"}
# The organization kinds a matched entity may already carry for each source kind (None: any kind).
ACCEPTS = {
    "vendor": ("company", "platform"),
    "customer": ("company", "nonprofit", "school", "platform"),
    "government": ("government", "agency", "court"),
    "other": None,
}
# The link from a source to self, when self exists.
LINK_TO_SELF = {"vendor": "vendor_to", "customer": "customer_of", "government": "governs"}
ORG_RULE_COLUMNS = (
    ("source_entity_id", "TEXT"),
    ("document_entity_id", "TEXT"),
    ("toward_scope", "TEXT NOT NULL DEFAULT '[]'"),
    ("applies_when", "TEXT"),
    ("party_side", "TEXT CHECK (party_side IN ('our_side', 'their_side'))"),
)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _columns(conn, table):
    # Only the fixed table names this file passes in.
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _new_entity_id(conn, name):
    """The store's id for a new organization: organization/<slug>, with -2, -3 ... when the slug is taken."""
    base = "organization/" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "source")
    candidate, n = base, 2
    while conn.execute("SELECT 1 FROM entities WHERE id = ?", (candidate,)).fetchone():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _kind_of(conn, entity_id):
    row = conn.execute(
        "SELECT option_key FROM entity_values WHERE entity_id = ? AND entity_attribute_id = 'attr_organization_kind'",
        (entity_id,),
    ).fetchone()
    return row[0] if row else None


def _link(conn, source_entity_id, link_key, to_entity_id, source_id, created_by, now):
    exists = conn.execute(
        "SELECT 1 FROM entity_links WHERE from_entity_id = ? AND link_key = ? AND to_entity_id = ?",
        (source_entity_id, link_key, to_entity_id),
    ).fetchone()
    if exists:
        return
    conn.execute(
        "INSERT INTO entity_links (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on, confidence, "
        "source_type, source_id, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, NULL, NULL, 0.9, 'rule_source', ?, '{}', ?, 'system', '', ?, ?)",
        (f"lnk_{uuid.uuid4().hex}", source_entity_id, link_key, to_entity_id, source_id, created_by, now, now),
    )


def _move_sources(conn, now):
    """Each rule_sources row onto an organization entity. Returns {old id: entity id}; empty when the table is gone."""
    if not _has_table(conn, "rule_sources"):
        return {}
    self_row = conn.execute("SELECT id FROM entities WHERE self = 1").fetchone()
    self_id = self_row[0] if self_row else None
    moved = {}
    sources = conn.execute("SELECT id, name, kind, created_by, created_at FROM rule_sources ORDER BY created_at, id")
    for source_id, name, kind, created_by, created_at in sources.fetchall():
        matches = conn.execute(
            "SELECT id, self FROM entities WHERE type = 'organization' AND name_key = lower(trim(?)) "
            "AND merged_into_id IS NULL AND deleted_at IS NULL ORDER BY id",
            (name,),
        ).fetchall()
        provenance = json.dumps({"id": source_id, "kind": kind, "created_by": created_by, "created_at": created_at})
        if any(row[1] for row in matches):
            raise RuntimeError(f"rule source {name!r} names the company itself (self): name its entity by hand first")
        if len(matches) > 1:
            ids = ", ".join(row[0] for row in matches)
            raise RuntimeError(
                f"rule source {name!r} matches {len(matches)} organizations ({ids}): choose one by hand first"
            )
        if matches:
            entity_id = matches[0][0]
            accepts = ACCEPTS[kind]
            current = _kind_of(conn, entity_id)
            if accepts is not None and current is not None and current not in accepts:
                raise RuntimeError(
                    f"rule source {name!r} is a {kind} but its one match, {entity_id}, is a {current}: fix the kind by hand"
                )
            taken = conn.execute(
                "SELECT 1 FROM entities WHERE id = ? AND json_extract(properties, '$.rule_source') IS NOT NULL",
                (entity_id,),
            ).fetchone()
            if taken:
                raise RuntimeError(
                    f"rule source {name!r} and another rule source both match {entity_id}: merge them first"
                )
            conn.execute(
                "UPDATE entities SET properties = json_set(CASE WHEN json_valid(properties) THEN properties ELSE '{}' END, "
                "'$.rule_source', json(?)), updated_at = ? WHERE id = ?",
                (provenance, now, entity_id),
            )
        else:
            entity_id = _new_entity_id(conn, name)
            conn.execute(
                "INSERT INTO entities (id, type, name, name_key, properties, created_by, created_by_type, created_by_id, "
                "created_at, updated_at) VALUES (?, 'organization', ?, lower(trim(?)), ?, ?, 'system', '', ?, ?)",
                (
                    entity_id,
                    name,
                    name,
                    json.dumps({"rule_source": json.loads(provenance)}),
                    created_by,
                    created_at,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, "
                "source_id, confidence, updated_at) VALUES (?, ?, 'attr_organization_kind', 1, ?, 'rule_source', ?, 0.9, ?)",
                (f"val_{entity_id}", entity_id, KIND_OF[kind], source_id, now),
            )
            conn.execute(
                "INSERT INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
                "VALUES (?, ?, 'rule_source', ?, 'company', ?)",
                (f"src_{uuid.uuid4().hex}", entity_id, source_id, now),
            )
        if self_id is not None and kind in LINK_TO_SELF:
            _link(conn, entity_id, LINK_TO_SELF[kind], self_id, source_id, created_by, now)
        moved[source_id] = entity_id
    return moved


def _org_rules(conn, moved):
    columns = _columns(conn, "org_rules")
    for name, definition in ORG_RULE_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE org_rules ADD COLUMN {name} {definition}")
    if "source_id" not in columns:
        return
    values = {row[0] for row in conn.execute("SELECT DISTINCT source_id FROM org_rules WHERE source_id IS NOT NULL")}
    unknown = sorted(values - set(moved))
    if unknown:
        raise RuntimeError(f"org_rules name source ids that are no rule source: {', '.join(unknown)}")
    for old, new in moved.items():
        conn.execute("UPDATE org_rules SET source_entity_id = ? WHERE source_id = ?", (new, old))
    conn.execute("DROP INDEX IF EXISTS org_rules_source")
    conn.execute("ALTER TABLE org_rules DROP COLUMN source_id")


def up(conn):
    now = _now()
    moved = _move_sources(conn, now)
    if _has_table(conn, "org_rules"):
        _org_rules(conn, moved)
    if moved and _has_table(conn, "rule_reads") and "source_id" in _columns(conn, "rule_reads"):
        for old, new in moved.items():
            conn.execute("UPDATE rule_reads SET source_id = ? WHERE source_id = ?", (new, old))
    if _has_table(conn, "rule_sources"):
        conn.execute("DROP TABLE rule_sources")
