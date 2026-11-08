"""Directory core (track 1): the market graph becomes the Directory's core tables, with every id and row kept.

- market_entities -> entities: the hot columns (name_key, primary_domain, owner, audience, created_by_type/id, deleted_at,
  ...), merged_into -> merged_into_id, and `seq`, an INTEGER PRIMARY KEY: the rowid the full-text index keys on, which a
  VACUUM cannot change (the implicit rowid of a TEXT-keyed table can).
- market_edges -> entity_links: src/rel/dst -> from_entity_id/link_key/to_entity_id, the relation keys mapped (employs ->
  works_at and led_by -> works_at with role leader, flipped; regulated_by -> regulates, flipped; invested_in -> invests_in),
  confidence high/medium/low -> 0.9/0.6/0.3 (REAL).
- market_entity_keys -> entity_identifiers: one row per name, domain or alias, with its kind inferred from the text. Not
  unique: a shared inbox or a parent and subsidiary on one domain is real, and a lookup that hits two entities is ambiguous.
- market_watches -> entity_watches (columns unchanged).
- market_fts is rebuilt on stable rowids: an entity's seq, an evidence row's fts_rowid (a negative number, so the two never
  meet). The delete triggers name a row by that rowid instead of scanning kind and ref.

The table renames are backend/migrations/renames_data.py's `directory` wave (scripts/cleanup/renames.yaml). A database
can hold an empty old-name table beside the new one (a create that ran after a rename); that table is dropped, and one
with rows is refused. Nothing else is guessed.

Old names stay in the migrations that ran before this one (0030_growth_segments reads market_edges), and they are not edited.
"""

from backend.migrations import renames as schema_renames

FOREIGN_KEYS_OFF = True
WAVE = "directory"
PAIRS = (
    ("market_entities", "entities"),
    ("market_edges", "entity_links"),
    ("market_entity_keys", "entity_identifiers"),
    ("market_watches", "entity_watches"),
)
# seq is the entity's rowid (INTEGER PRIMARY KEY): the full-text index keys on it, and it is kept by the copy below.
ENTITIES = """CREATE TABLE entities_next (
  seq INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '[]',
  external_ids TEXT NOT NULL DEFAULT '{}',
  tier TEXT,
  summary TEXT NOT NULL DEFAULT '',
  properties TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  merged_into_id TEXT,
  last_verified_at TEXT,
  primary_email TEXT,
  primary_domain TEXT,
  owner_type TEXT,
  owner_id TEXT,
  audience TEXT NOT NULL DEFAULT 'company',
  self_link_keys TEXT NOT NULL DEFAULT '',
  last_activity_at TEXT,
  mentions_count INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL,
  created_by_type TEXT NOT NULL DEFAULT 'system',
  created_by_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT)"""
ENTITIES_COPY = """INSERT INTO entities_next (seq, id, type, name, name_key, aliases, external_ids, tier, summary, properties,
  status, merged_into_id, last_verified_at, primary_domain, created_by, created_by_type, created_by_id, created_at,
  updated_at)
SELECT rowid, id, type, name, lower(trim(name)), aliases, external_ids, tier, summary, properties, status, merged_into,
  last_verified_at,
  NULLIF(lower(trim(CASE WHEN json_valid(external_ids) THEN json_extract(external_ids, '$.domain') END)), ''),
  created_by, {actor_type}, {actor_id}, created_at, updated_at
FROM entities"""
ENTITY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS entities_name ON entities(name, id)",
    "CREATE INDEX IF NOT EXISTS entities_status_name ON entities(status, name, id)",
    "CREATE INDEX IF NOT EXISTS entities_type_name ON entities(type, name, id)",
    "CREATE INDEX IF NOT EXISTS entities_name_key ON entities(name_key)",
    "CREATE INDEX IF NOT EXISTS entities_primary_domain ON entities(primary_domain) WHERE primary_domain IS NOT NULL",
)
LINKS = """CREATE TABLE entity_links_next (
  id TEXT PRIMARY KEY,
  from_entity_id TEXT NOT NULL,
  link_key TEXT NOT NULL,
  to_entity_id TEXT NOT NULL,
  valid_from_on TEXT,
  valid_until_on TEXT,
  confidence REAL NOT NULL DEFAULT 0.6,
  source_type TEXT,
  source_id TEXT,
  properties TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL,
  created_by_type TEXT NOT NULL DEFAULT 'system',
  created_by_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL)"""
# employs (org -> person) and led_by (company -> person) read person works_at org; regulated_by (company -> regulation)
# reads regulation regulates company. The flipped ones swap src and dst.
FLIPPED = ("employs", "led_by", "regulated_by")
LINKS_COPY = """INSERT INTO entity_links_next (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on,
  confidence, source_type, source_id, properties, created_by, created_by_type, created_by_id, created_at, updated_at)
SELECT id,
  CASE WHEN rel IN ('employs', 'led_by', 'regulated_by') THEN dst ELSE src END,
  CASE rel WHEN 'employs' THEN 'works_at' WHEN 'led_by' THEN 'works_at' WHEN 'regulated_by' THEN 'regulates'
    WHEN 'invested_in' THEN 'invests_in' ELSE rel END,
  CASE WHEN rel IN ('employs', 'led_by', 'regulated_by') THEN src ELSE dst END,
  valid_from_on, valid_until_on,
  CASE confidence WHEN 'high' THEN 0.9 WHEN 'medium' THEN 0.6 WHEN 'low' THEN 0.3 ELSE 0.6 END,
  NULL, NULL,
  CASE WHEN rel = 'led_by' THEN json_set(CASE WHEN json_valid(properties) THEN properties ELSE '{{}}' END, '$.role', 'leader')
    ELSE properties END,
  created_by, {actor_type}, {actor_id}, created_at, updated_at
FROM entity_links"""
LINK_INDEXES = (
    "CREATE INDEX IF NOT EXISTS entity_links_from ON entity_links(from_entity_id, link_key)",
    "CREATE INDEX IF NOT EXISTS entity_links_to ON entity_links(to_entity_id, link_key)",
    "CREATE INDEX IF NOT EXISTS entity_links_until ON entity_links(valid_until_on)",
    "CREATE INDEX IF NOT EXISTS entity_links_key_pair ON entity_links(link_key, from_entity_id, to_entity_id)",
)
IDENTIFIERS = """CREATE TABLE entity_identifiers_next (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  normalized_value TEXT NOT NULL,
  scope_type TEXT,
  scope_id TEXT,
  primary_identifier INTEGER NOT NULL DEFAULT 0,
  source_type TEXT,
  source_id TEXT,
  valid_from_on TEXT,
  valid_until_on TEXT)"""
IDENTIFIER_INDEXES = (
    "CREATE INDEX IF NOT EXISTS entity_identifiers_value ON entity_identifiers(normalized_value, kind)",
    "CREATE INDEX IF NOT EXISTS entity_identifiers_entity ON entity_identifiers(entity_id)",
    "CREATE INDEX IF NOT EXISTS entity_identifiers_unicode ON entity_identifiers(entity_id) "
    "WHERE normalized_value GLOB '*[^ -~]*'",
)
WATCH_INDEXES = (
    "CREATE INDEX IF NOT EXISTS entity_watches_active ON entity_watches(active, checked_at)",
    "CREATE INDEX IF NOT EXISTS entity_watches_entity ON entity_watches(entity_id)",
)
OLD_TRIGGERS = (
    "market_entities_ai",
    "market_entities_ad",
    "market_entities_au",
    "market_evidence_ai",
    "market_evidence_ad",
    "market_evidence_au",
    "market_keys_insert",
    "market_keys_update",
    "market_keys_delete",
)
FTS = """CREATE VIRTUAL TABLE market_fts USING fts5(
  kind UNINDEXED,
  ref UNINDEXED,
  name,
  aliases,
  summary,
  quote,
  our_read,
  tokenize = 'unicode61 remove_diacritics 1')"""


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _infer(expr):
    """The kind of an identifier from its text (lower case, trimmed): the rule the triggers and this migration share."""
    return (
        f"CASE WHEN {expr} LIKE 'http://%' OR {expr} LIKE 'https://%' THEN 'url' "
        f"WHEN instr({expr}, '@') > 1 AND instr({expr}, ' ') = 0 THEN 'email' "
        f"WHEN {expr} GLOB '+[0-9]*' AND {expr} NOT GLOB '*[^0-9+() .-]*' THEN 'phone' "
        f"WHEN instr({expr}, '.') > 0 AND instr({expr}, ' ') = 0 AND {expr} NOT GLOB '*[^a-z0-9.-]*' THEN 'domain' "
        "ELSE 'name' END"
    )


def _actor(expr):
    """`created_by` is `type:id` ("bot:librarian", "human:ana"); a bare value such as "seed" is the system's."""
    colon = f"instr({expr}, ':')"
    return (
        f"CASE WHEN {colon} > 0 THEN substr({expr}, 1, {colon} - 1) ELSE 'system' END",
        f"CASE WHEN {colon} > 0 THEN substr({expr}, {colon} + 1) ELSE {expr} END",
    )


def _identifier_rows(ref):
    """The identifier rows one entity row (`ref`, a trigger's new or an entities alias) makes: its name, its domain, then
    each alias. A key two of them share is one row, the first kind it names and primary if any did."""
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
        "INSERT INTO entity_identifiers (id, entity_id, kind, value, normalized_value, primary_identifier) "
        f"SELECT 'idn_' || lower(hex(randomblob(8))), {ref}.id, kind, key, key, prim FROM ({_identifier_rows(ref)})"
    )


def _rebuild(conn, name, create, copy, indexes=()):
    """Make `name` the new shape: copy its rows (when it exists) into a table of the new shape, drop the old, rename."""
    conn.execute(create)
    if _table(conn, name):
        conn.execute(copy)
        conn.execute(f'DROP TABLE "{name}"')
    conn.execute(f'ALTER TABLE "{name}_next" RENAME TO "{name}"')
    for statement in indexes:
        conn.execute(statement)


def _refuse_shells(conn):
    """An old-name table beside its new one: an empty one is a shell and goes; one with rows is two sets of data, and the
    migration does not choose between them."""
    for old, new in PAIRS:
        if _table(conn, old) and _table(conn, new):
            if conn.execute(f'SELECT count(*) FROM "{old}"').fetchone()[0]:
                raise RuntimeError(
                    f"both {old} and {new} hold rows: the Directory migration will not choose between them"
                )
            conn.execute(f'DROP TABLE "{old}"')


def _moved(conn):
    """The Directory's tables are already in their new shape (an earlier boot ran this file). Then nothing is left to move,
    and a rerun changes nothing: a migration that is replayed on a finished database must not copy from the new tables."""
    return (
        _table(conn, "entities")
        and "merged_into_id" in _columns(conn, "entities")
        and not any(_table(conn, old) for old, _new in PAIRS)
    )


def up(conn):
    if _moved(conn):
        return
    _refuse_shells(conn)
    # A rename re-checks every trigger, and on a new file a trigger can name a table the module code makes after the
    # migrations (presences_meeting_delete names task_roles). So the triggers step aside for the renames and the rebuilds
    # and go back as they were; the market's own, which name the old tables, are made again below.
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    for name, _sql in triggers:
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    schema_renames.apply(conn, waves=(WAVE,))

    actor_type, actor_id = _actor("created_by")
    _rebuild(conn, "entities", ENTITIES, ENTITIES_COPY.format(actor_type=actor_type, actor_id=actor_id), ENTITY_INDEXES)

    actor_type, actor_id = _actor("created_by")
    _rebuild(conn, "entity_links", LINKS, LINKS_COPY.format(actor_type=actor_type, actor_id=actor_id), LINK_INDEXES)

    # Identifiers are copied from market_entity_keys (key, entity_id): the kind comes from the text, a domain from the
    # entity's domain, and primary marks the entity's own name or domain.
    identifiers_copy = (
        "INSERT INTO entity_identifiers_next (id, entity_id, kind, value, normalized_value, primary_identifier) "
        "SELECT 'idn_' || lower(hex(randomblob(8))), k.entity_id, "
        "CASE WHEN EXISTS (SELECT 1 FROM entities e WHERE e.id = k.entity_id AND e.primary_domain = k.key) "
        f"THEN 'domain' ELSE {_infer('k.key')} END, k.key, k.key, "
        "CASE WHEN EXISTS (SELECT 1 FROM entities e WHERE e.id = k.entity_id AND (e.name_key = k.key "
        "OR e.primary_domain = k.key)) THEN 1 ELSE 0 END FROM entity_identifiers k"
    )
    _rebuild(conn, "entity_identifiers", IDENTIFIERS, identifiers_copy, IDENTIFIER_INDEXES)

    conn.execute("DROP INDEX IF EXISTS market_watches_active")
    conn.execute("DROP INDEX IF EXISTS market_watches_entity")
    if _table(conn, "entity_watches"):
        for statement in WATCH_INDEXES:
            conn.execute(statement)

    for name, sql in triggers:
        if name not in OLD_TRIGGERS:
            conn.execute(sql)
    # The entity, identifier and evidence triggers are made here, each on the rowid it keys on.
    conn.execute(f"CREATE TRIGGER entities_identifiers_ai AFTER INSERT ON entities BEGIN {_sync_insert('new')}; END")
    conn.execute(
        "CREATE TRIGGER entities_identifiers_au AFTER UPDATE OF name, aliases, external_ids ON entities BEGIN "
        f"DELETE FROM entity_identifiers WHERE entity_id = old.id; {_sync_insert('new')}; END"
    )
    conn.execute(
        "CREATE TRIGGER entities_identifiers_ad AFTER DELETE ON entities BEGIN "
        "DELETE FROM entity_identifiers WHERE entity_id = old.id; END"
    )

    # The full-text index: rebuilt on stable rowids. Entities key on seq, evidence on fts_rowid (negative, so they never meet).
    conn.execute("DROP TABLE IF EXISTS market_fts")
    conn.execute(FTS)
    if "fts_rowid" not in _columns(conn, "market_evidence"):
        conn.execute("ALTER TABLE market_evidence ADD COLUMN fts_rowid INTEGER")
    conn.execute("UPDATE market_evidence SET fts_rowid = -rowid WHERE fts_rowid IS NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS market_evidence_fts_rowid ON market_evidence(fts_rowid)")
    conn.execute("""CREATE TRIGGER entities_fts_ai AFTER INSERT ON entities BEGIN
  INSERT INTO market_fts(rowid, kind, ref, name, aliases, summary, quote, our_read)
  VALUES (new.seq, 'entity', new.id, new.name, new.aliases, new.summary, '', ''); END""")
    conn.execute("""CREATE TRIGGER entities_fts_ad AFTER DELETE ON entities BEGIN
  DELETE FROM market_fts WHERE rowid = old.seq; END""")
    conn.execute("""CREATE TRIGGER entities_fts_au AFTER UPDATE OF name, aliases, summary ON entities BEGIN
  DELETE FROM market_fts WHERE rowid = old.seq;
  INSERT INTO market_fts(rowid, kind, ref, name, aliases, summary, quote, our_read)
  VALUES (new.seq, 'entity', new.id, new.name, new.aliases, new.summary, '', ''); END""")
    conn.execute("""CREATE TRIGGER market_evidence_ai AFTER INSERT ON market_evidence BEGIN
  INSERT INTO market_fts(rowid, kind, ref, name, aliases, summary, quote, our_read)
  VALUES (new.fts_rowid, 'evidence', new.id, '', '', '', new.quote, new.our_read); END""")
    conn.execute("""CREATE TRIGGER market_evidence_ad AFTER DELETE ON market_evidence BEGIN
  DELETE FROM market_fts WHERE rowid = old.fts_rowid; END""")
    conn.execute("""CREATE TRIGGER market_evidence_au AFTER UPDATE OF quote, our_read ON market_evidence BEGIN
  DELETE FROM market_fts WHERE rowid = old.fts_rowid;
  INSERT INTO market_fts(rowid, kind, ref, name, aliases, summary, quote, our_read)
  VALUES (new.fts_rowid, 'evidence', new.id, '', '', '', new.quote, new.our_read); END""")
    conn.execute(
        "INSERT INTO market_fts(rowid, kind, ref, name, aliases, summary, quote, our_read) "
        "SELECT seq, 'entity', id, name, aliases, summary, '', '' FROM entities"
    )
    conn.execute(
        "INSERT INTO market_fts(rowid, kind, ref, name, aliases, summary, quote, our_read) "
        "SELECT fts_rowid, 'evidence', id, '', '', '', quote, our_read FROM market_evidence"
    )
