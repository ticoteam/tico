"""Directory tables (track 3): the data model the Directory's store, tools and read rule work from.

Built on track 1 (20261108011017_directory_core), which renamed the market graph into entities, entity_links,
entity_identifiers and entity_watches. This migration:

- entities: `self` (exactly one organization is ours: a partial unique index). The types move to the Directory's keys:
  company -> organization (kind company, below), geography -> place, regulation -> document. Ids are kept, and `type`
  stays the text key of entity_types (there is no entity_type_id).
- entity_identifiers.derived: 1 for the rows the name, domain and alias triggers make. Track 1's triggers deleted every
  identifier of an entity when its name, aliases or external ids changed, which would take a store-made email with them.
  The two triggers are replaced here so they only replace derived rows. Every row that exists now is derived.
- entity_links.evidence_id: the market_evidence row a link rests on.
- New tables: entity_types, link_types, entity_attributes, entity_values (current values; one row per single-value
  field), entity_value_history (append-only), entity_external_refs, entity_activities, entity_connections, entity_notes,
  entity_views, entity_mentions, entity_suppressions, entity_sources (what an entity is known from: the read rule's
  input) and entity_merges (the undo log of a merge: which rows moved where; not in the spec, which left merges to
  history).
- Seeds: the built-in entity types (deal archived), the built-in link types (the spec's table plus every key the market's
  RELATIONS use), and organization.kind. Entity types the data holds that are not built in get a row too.

The trigger SQL is copied from track 1 rather than imported: a migration is a frozen snapshot, and module names that
start with a version cannot be imported by name.
"""

import json
from backend import hubdb as H

FOREIGN_KEYS_OFF = False  # every change here adds a table or a column; nothing is rebuilt

VALUE_TYPES = (
    "text",
    "number",
    "currency",
    "date",
    "timestamp",
    "checkbox",
    "select",
    "status",
    "email",
    "phone",
    "domain",
    "url",
    "personal_name",
    "rating",
    "actor",
)
ORGANIZATION_KINDS = (
    ("company", "Company"),
    ("government", "Government"),
    ("agency", "Agency"),
    ("court", "Court"),
    ("nonprofit", "Nonprofit"),
    ("school", "School"),
    ("platform", "Platform"),
    ("community", "Community"),
)
# The market's old type keys and the Directory's.
TYPE_RENAMES = (("company", "organization"), ("geography", "place"), ("regulation", "document"))

BUILT_IN_TYPES = (
    (
        "person",
        "Person",
        "People",
        "A person Tico works with: a customer or prospect, an advisor, a reporter, a counterpart. A teammate works at "
        "self.",
        False,
    ),
    (
        "organization",
        "Organization",
        "Organizations",
        "A company, government, agency, court, nonprofit, school, platform or community. Its kind is the kind attribute.",
        False,
    ),
    (
        "place",
        "Place",
        "Places",
        "A country, US state, county or city. Laws apply to places, and we operate in them.",
        False,
    ),
    (
        "document",
        "Document",
        "Documents",
        "A law, regulation, contract or terms of service. Its issuer is an organization, and its parties link to it with "
        "party_to.",
        False,
    ),
    ("product", "Product", "Products", "A product or service: ours (a link to self) or a competitor's.", False),
    ("segment", "Segment", "Segments", "A group of buyers the market graph tracks.", False),
    (
        "channel",
        "Channel",
        "Channels",
        "A place where buyers or owners talk: a forum, a feed, a trade association's list.",
        False,
    ),
    ("event", "Event", "Events", "Something that happened or will: a lawsuit, a launch, a hearing, a deadline.", False),
    (
        "deal",
        "Deal",
        "Deals",
        "A sales opportunity. Off by default: archived until a company turns it on.",
        True,
    ),
)
# key, name, inverse name, from type, to type (NULL = any), inherits rules, symmetric, description
BUILT_IN_LINKS = (
    (
        "works_at",
        "works at",
        "employs",
        "person",
        "organization",
        1,
        0,
        "A person's job at an organization. Rules toward the organization reach the person. properties.role names a "
        "leader's role.",
    ),
    (
        "member_of",
        "member of",
        "has member",
        None,
        None,
        1,
        0,
        "Membership: a person or an organization in an organization or a channel (a trade association, a board).",
    ),
    (
        "subsidiary_of",
        "subsidiary of",
        "parent of",
        "organization",
        "organization",
        1,
        0,
        "One organization is part of another. Rules toward the parent reach the subsidiary.",
    ),
    (
        "located_in",
        "located in",
        "contains",
        None,
        "place",
        1,
        0,
        "A person, organization or place sits inside a place. Laws for the place reach what is inside it.",
    ),
    (
        "operates_in",
        "operates in",
        "has operations in",
        "organization",
        "place",
        1,
        0,
        "An organization does business in a place. This is how a state's laws reach self.",
    ),
    (
        "governs",
        "governs",
        "governed by",
        "organization",
        "place",
        0,
        0,
        "A government body is the authority for a place. It marks the jurisdiction; rules do not pass through it.",
    ),
    (
        "issued_by",
        "issued by",
        "issues",
        "document",
        "organization",
        0,
        0,
        "A law, regulation or contract comes from an organization.",
    ),
    (
        "party_to",
        "party to",
        "has party",
        None,
        "document",
        1,
        0,
        "An organization or a person is a party to a document. properties.side is our_side or their_side: a duty reaches "
        "only the party that owes it.",
    ),
    (
        "customer_of",
        "customer of",
        "customers",
        None,
        None,
        0,
        0,
        "Buys from self. A link to self: the source says whose records it comes from (Close, Finance).",
    ),
    ("vendor_to", "vendor to", "vendors", None, None, 0, 0, "Sells to self. A link to self."),
    ("partner_of", "partner of", "partners", None, None, 0, 0, "A partner of self. A link to self."),
    (
        "invests_in",
        "invests in",
        "investors",
        None,
        None,
        0,
        0,
        "Invests in the other end. To self when it is an investor in us.",
    ),
    ("advises", "advises", "advised by", None, None, 0, 0, "Advises the other end. To self when it advises us."),
    ("represents", "represents", "represented by", None, None, 0, 0, "Acts for the other end (counsel, an agent)."),
    (
        "regulates",
        "regulates",
        "regulated by",
        None,
        None,
        0,
        0,
        "Holds authority over the other end. The regulator is the from end.",
    ),
    (
        "competes_with",
        "competes with",
        "competes with",
        None,
        None,
        0,
        1,
        "Competes with the other end. Symmetric: stored once, read from either end.",
    ),
    ("sells_to", "sells to", "buys from", None, None, 0, 0, "Sells to the other end."),
    (
        "owns",
        "owns",
        "owned by",
        None,
        None,
        0,
        0,
        "Owns the other end: a site, a product, a stake.",
    ),
    (
        "referred_by",
        "referred by",
        "referred",
        "person",
        "person",
        0,
        0,
        "A person was referred to us by another person.",
    ),
    (
        "introduced_by",
        "introduced by",
        "introduced",
        "person",
        "person",
        0,
        0,
        "A person was introduced to us by another person.",
    ),
    # The market's own keys (RELATIONS in backend/market.py). Not inherited, typed loosely: the market wrote them as it
    # saw fit before the Directory had types.
    (
        "partners_with",
        "partners with",
        "partners with",
        None,
        None,
        0,
        1,
        "Partners with the other end. Symmetric: stored once, read from either end.",
    ),
    (
        "integrates_with",
        "integrates with",
        "integrated by",
        None,
        None,
        0,
        0,
        "Its product integrates with the other end.",
    ),
    (
        "distributes_through",
        "distributes through",
        "distributes",
        None,
        None,
        0,
        0,
        "Reaches buyers through the other end (a channel, a marketplace).",
    ),
    ("acquired", "acquired", "acquired by", None, None, 0, 0, "Acquired the other end."),
    ("founded", "founded", "founded by", None, None, 0, 0, "A person founded the other end."),
    (
        "formerly",
        "formerly",
        "now",
        None,
        None,
        0,
        0,
        "The from end was the other end under another name, or one became the other.",
    ),
    ("subject_of", "subject of", "subjects", None, None, 0, 0, "The from end is the subject of the other end."),
    ("mentioned_in", "mentioned in", "mentions", None, None, 0, 0, "The from end is mentioned in the other end."),
)

CREATE = (
    """CREATE TABLE IF NOT EXISTS entity_types (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  plural_name TEXT NOT NULL DEFAULT '',
  icon TEXT NOT NULL DEFAULT '',
  built_in INTEGER NOT NULL DEFAULT 0,
  mode TEXT NOT NULL DEFAULT 'native' CHECK (mode IN ('native', 'linked')),
  linked_connection_id TEXT,
  description TEXT NOT NULL DEFAULT '',
  created_by_type TEXT NOT NULL DEFAULT 'system',
  created_by_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  archived_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS link_types (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  inverse_name TEXT NOT NULL DEFAULT '',
  from_entity_type TEXT REFERENCES entity_types(key),
  to_entity_type TEXT REFERENCES entity_types(key),
  inherits_rules INTEGER NOT NULL DEFAULT 0,
  symmetric INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  built_in INTEGER NOT NULL DEFAULT 0,
  created_by_type TEXT NOT NULL DEFAULT 'system',
  created_by_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  archived_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS entity_attributes (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL REFERENCES entity_types(key),
  key TEXT NOT NULL,
  name TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK (value_type IN ({value_types})),
  multiple INTEGER NOT NULL DEFAULT 0,
  unique_value INTEGER NOT NULL DEFAULT 0,
  required INTEGER NOT NULL DEFAULT 0,
  options TEXT NOT NULL DEFAULT '[]',
  owner_type TEXT NOT NULL DEFAULT 'tico',
  owner_id TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  sensitive INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0,
  created_by_type TEXT NOT NULL DEFAULT 'system',
  created_by_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  archived_at TEXT,
  UNIQUE (entity_type, key))""".replace("{value_types}", ", ".join(f"'{v}'" for v in VALUE_TYPES)),
    """CREATE TABLE IF NOT EXISTS entity_values (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  entity_attribute_id TEXT NOT NULL REFERENCES entity_attributes(id),
  single_value INTEGER NOT NULL DEFAULT 1,
  text_value TEXT,
  number_value REAL,
  currency TEXT,
  date_value TEXT,
  timestamp_value TEXT,
  option_key TEXT,
  json_value TEXT,
  source_type TEXT,
  source_id TEXT,
  evidence_id TEXT,
  confidence REAL NOT NULL DEFAULT 0.6,
  updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS entity_value_history (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  entity_attribute_id TEXT NOT NULL REFERENCES entity_attributes(id),
  value TEXT NOT NULL,  -- JSON
  source_type TEXT,
  source_id TEXT,
  evidence_id TEXT,
  confidence REAL,
  active_from TEXT NOT NULL,
  active_until TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS entity_external_refs (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  connection_id TEXT NOT NULL DEFAULT '',
  system TEXT NOT NULL,
  external_type TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT '',
  synced_at TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE (system, external_type, external_id))""",
    """CREATE TABLE IF NOT EXISTS entity_activities (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  kind TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  subject_type TEXT,
  subject_id TEXT,
  external_ref TEXT,
  actor_type TEXT,
  actor_id TEXT,
  direction TEXT,
  summary TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS entity_connections (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  actor_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  first_interaction_at TEXT,
  last_interaction_at TEXT,
  interactions_count INTEGER NOT NULL DEFAULT 0,
  strength REAL NOT NULL DEFAULT 0,
  UNIQUE (entity_id, actor_type, actor_id))""",
    """CREATE TABLE IF NOT EXISTS entity_notes (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  author_type TEXT NOT NULL,
  author_id TEXT NOT NULL,
  body TEXT NOT NULL,
  audience TEXT NOT NULL DEFAULT 'company' CHECK (audience IN ('me', 'company')),
  pinned_at TEXT,
  evidence_id TEXT,
  created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS entity_views (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL REFERENCES entity_types(key),
  name TEXT NOT NULL,
  layout TEXT NOT NULL DEFAULT 'list',
  filters TEXT NOT NULL DEFAULT '{}',
  columns TEXT NOT NULL DEFAULT '[]',
  sort TEXT NOT NULL DEFAULT '',
  group_attribute_id TEXT REFERENCES entity_attributes(id),
  owner_type TEXT NOT NULL DEFAULT 'system',
  owner_id TEXT NOT NULL DEFAULT '',
  shared INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS entity_mentions (
  id TEXT PRIMARY KEY,
  mentionable_type TEXT NOT NULL,
  mentionable_id TEXT NOT NULL,
  mentionable_revision TEXT NOT NULL DEFAULT '',
  entity_id TEXT NOT NULL REFERENCES entities(id),
  role TEXT NOT NULL DEFAULT 'mentioned' CHECK (role IN ('subject', 'mentioned')),
  quote TEXT NOT NULL DEFAULT '',
  occurrence INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0.6,
  method TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'linked',
  created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS entity_suppressions (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  value_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  UNIQUE (kind, value_hash))""",
    """CREATE TABLE IF NOT EXISTS entity_sources (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  visibility TEXT NOT NULL CHECK (visibility IN ('company', 'restricted')),
  created_at TEXT NOT NULL,
  UNIQUE (entity_id, source_type, source_id))""",
    """CREATE TABLE IF NOT EXISTS entity_merges (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  into_entity_id TEXT NOT NULL REFERENCES entities(id),
  moved TEXT NOT NULL DEFAULT '{}',
  prior TEXT NOT NULL DEFAULT '{}',
  actor_type TEXT NOT NULL DEFAULT 'system',
  actor_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  undone_at TEXT)""",
)
INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS entities_self ON entities(self) WHERE self = 1",
    "CREATE INDEX IF NOT EXISTS entities_type_name_key ON entities(type, name_key, id)",
    "CREATE INDEX IF NOT EXISTS entities_type_activity ON entities(type, last_activity_at, id)",
    "CREATE INDEX IF NOT EXISTS entities_primary_email ON entities(primary_email) WHERE primary_email IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS entities_owner ON entities(owner_type, owner_id) WHERE owner_id <> ''",
    "CREATE INDEX IF NOT EXISTS entity_values_entity ON entity_values(entity_id)",
    "CREATE INDEX IF NOT EXISTS entity_values_attribute_text ON entity_values(entity_attribute_id, text_value)",
    "CREATE INDEX IF NOT EXISTS entity_values_attribute_option ON entity_values(entity_attribute_id, option_key)",
    "CREATE UNIQUE INDEX IF NOT EXISTS entity_values_single ON entity_values(entity_id, entity_attribute_id) "
    "WHERE single_value = 1",
    "CREATE INDEX IF NOT EXISTS entity_value_history_entity ON entity_value_history(entity_id, entity_attribute_id, "
    "active_until)",
    "CREATE INDEX IF NOT EXISTS entity_external_refs_entity ON entity_external_refs(entity_id)",
    "CREATE INDEX IF NOT EXISTS entity_activities_entity ON entity_activities(entity_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS entity_connections_last ON entity_connections(entity_id, last_interaction_at)",
    "CREATE INDEX IF NOT EXISTS entity_notes_entity ON entity_notes(entity_id, created_at)",
    "CREATE INDEX IF NOT EXISTS entity_mentions_entity ON entity_mentions(entity_id, created_at)",
    "CREATE INDEX IF NOT EXISTS entity_mentions_mentionable ON entity_mentions(mentionable_type, mentionable_id)",
    "CREATE INDEX IF NOT EXISTS entity_sources_source ON entity_sources(source_type, source_id)",
    "CREATE INDEX IF NOT EXISTS entity_merges_entity ON entity_merges(entity_id, created_at)",
)

# Track 1's identifier triggers, with `derived`: they replace only the rows they make.
IDENTIFIER_TRIGGERS = ("entities_identifiers_ai", "entities_identifiers_au")


def _now():
    # The hub's clock, not the machine's: a demo build runs at its own moment and its rows must not postdate it.
    return H.now()


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _infer(expr):
    """The kind of an identifier from its text (lower case, trimmed), as track 1 infers it."""
    return (
        f"CASE WHEN {expr} LIKE 'http://%' OR {expr} LIKE 'https://%' THEN 'url' "
        f"WHEN instr({expr}, '@') > 1 AND instr({expr}, ' ') = 0 THEN 'email' "
        f"WHEN {expr} GLOB '+[0-9]*' AND {expr} NOT GLOB '*[^0-9+() .-]*' THEN 'phone' "
        f"WHEN instr({expr}, '.') > 0 AND instr({expr}, ' ') = 0 AND {expr} NOT GLOB '*[^a-z0-9.-]*' THEN 'domain' "
        "ELSE 'name' END"
    )


def _identifier_rows(ref):
    """The identifier rows one entity row (`ref`, a trigger's new) makes: its name, its domain, then each alias."""
    name = f"lower(trim({ref}.name))"
    domain = (
        f"CASE WHEN json_valid({ref}.external_ids) THEN lower(trim(json_extract({ref}.external_ids, '$.domain'))) END"
    )
    return f"""SELECT key, MIN(kind) AS kind, MAX(prim) AS prim FROM (
    SELECT {name} AS key, {_infer(name)} AS kind, 1 AS prim
    UNION ALL SELECT {domain}, 'domain', 1
    UNION ALL SELECT lower(trim(j.value)), {_infer('lower(trim(j.value))')}, 0
      FROM json_each(CASE WHEN json_valid({ref}.aliases) THEN {ref}.aliases ELSE '[]' END) j
  ) WHERE key IS NOT NULL AND key <> '' GROUP BY key"""


def _sync_insert(ref):
    return (
        "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
        f"SELECT {ref}.id, kind, key, key, prim, 1 FROM ({_identifier_rows(ref)}) ORDER BY key"
    )


def _add_columns(conn):
    if "self" not in _columns(conn, "entities"):
        conn.execute("ALTER TABLE entities ADD COLUMN self INTEGER NOT NULL DEFAULT 0")
    if "evidence_id" not in _columns(conn, "entity_links"):
        conn.execute("ALTER TABLE entity_links ADD COLUMN evidence_id TEXT")
    if "derived" not in _columns(conn, "entity_identifiers"):
        conn.execute("ALTER TABLE entity_identifiers ADD COLUMN derived INTEGER NOT NULL DEFAULT 0")
        # Every identifier that exists now came from the triggers below (track 1 wrote none by hand).
        conn.execute("UPDATE entity_identifiers SET derived = 1")


def _identifier_triggers(conn):
    for name in IDENTIFIER_TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute(f"CREATE TRIGGER entities_identifiers_ai AFTER INSERT ON entities BEGIN {_sync_insert('new')}; END")
    conn.execute(
        "CREATE TRIGGER entities_identifiers_au AFTER UPDATE OF name, aliases, external_ids ON entities BEGIN "
        f"DELETE FROM entity_identifiers WHERE entity_id = old.id AND derived = 1; {_sync_insert('new')}; END"
    )


def _seed_types(conn, now):
    for key, name, plural, description, archived in BUILT_IN_TYPES:
        conn.execute(
            "INSERT OR IGNORE INTO entity_types (id, key, name, plural_name, icon, built_in, mode, description, "
            "created_by_type, created_by_id, created_at, archived_at) VALUES (?,?,?,?,'',1,'native',?,'system','',?,?)",
            (f"etype_{key}", key, name, plural, description, now, now if archived else None),
        )


def _seed_kind(conn, now):
    """organization.kind: the one built-in attribute. The companies the market holds get kind company, now, from their
    type, so the rename below loses nothing."""
    conn.execute(
        "INSERT OR IGNORE INTO entity_attributes (id, entity_type, key, name, value_type, multiple, unique_value, "
        "required, options, owner_type, owner_id, description, sensitive, position, created_by_type, created_by_id, "
        "created_at) VALUES ('attr_organization_kind', 'organization', 'kind', 'Kind', 'select', 0, 0, 0, ?, 'tico', '', "
        "'What kind of organization this is. The kind decides which laws and rules read it.', 0, 0, 'system', '', ?)",
        (json.dumps([{"key": key, "name": name} for key, name in ORGANIZATION_KINDS]), now),
    )
    conn.execute(
        "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, source_id, "
        "confidence, updated_at) SELECT 'val_' || id, id, 'attr_organization_kind', 1, 'company', "
        "'market', '', 0.9, ? FROM entities WHERE type = 'company' "
        "AND NOT EXISTS (SELECT 1 FROM entity_values v WHERE v.entity_id = entities.id "
        "AND v.entity_attribute_id = 'attr_organization_kind')",
        (now,),
    )


def _remap(conn):
    for old, new in TYPE_RENAMES:
        conn.execute("UPDATE entities SET type = ? WHERE type = ?", (new, old))


def _seed_unknown_types(conn, now):
    """A type the data holds that is not built in (a Market type this list does not know) gets a row, so the store can
    name it. Its description says to write one."""
    built_in = [key for key, *_rest in BUILT_IN_TYPES]
    marks = ",".join("?" for _ in built_in)
    conn.execute(
        "INSERT OR IGNORE INTO entity_types (id, key, name, plural_name, icon, built_in, mode, description, "
        "created_by_type, created_by_id, created_at) "
        "SELECT 'etype_' || type, type, type, '', '', 0, 'native', 'Found in the market graph. Describe it.', "
        f"'system', '', ? FROM (SELECT DISTINCT type FROM entities WHERE type NOT IN ({marks}))",
        [now, *built_in],
    )


def _seed_link_types(conn, now):
    for key, name, inverse, source, target, inherits, symmetric, description in BUILT_IN_LINKS:
        conn.execute(
            "INSERT OR IGNORE INTO link_types (id, key, name, inverse_name, from_entity_type, to_entity_type, "
            "inherits_rules, symmetric, description, built_in, created_by_type, created_by_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,1,'system','',?)",
            (f"ltype_{key}", key, name, inverse, source, target, inherits, symmetric, description, now),
        )


def up(conn):
    now = _now()
    for statement in CREATE:
        conn.execute(statement)
    _add_columns(conn)
    _identifier_triggers(conn)
    for statement in INDEXES:
        conn.execute(statement)
    _seed_types(conn, now)
    _seed_kind(conn, now)
    _remap(conn)
    _seed_unknown_types(conn, now)
    _seed_link_types(conn, now)
