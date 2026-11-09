"""Stable bot ids: `bots` gains `id`, its primary key, and `slug` becomes a unique label that may be renamed. Every
existing bot gets id = slug, which freezes its current name as its id, so no stored actor string, reference, history row
or queued payload changes. The tables that referred to bots(slug) are rebuilt to refer to bots(id), and the principals
table and the bot identity triggers are added.

A new file already has the new `bots` (hubdb SCHEMA) and the module tables are created after this with bots(id); here it
moves the references hubdb's own steps made (chat_goals) and adds the principals table and the triggers. It rebuilds
tables, so foreign keys are off for this file.
"""
import re

from backend import migrations

FOREIGN_KEYS_OFF = True

# A bot's id never changes and its slug is a label that may; no slug may name another bot's id, so a lookup by id or
# slug is never ambiguous. A principal is an actor string's identity: `human:<id>`, `bot:<bot id>`, a system actor. Its
# rows are kept with their humans and bots, and a deleted one keeps its row with deleted_at set.
SCHEMA = """
CREATE TABLE IF NOT EXISTS principals (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('human','bot','agent','service','system')), deleted_at TEXT);
CREATE TRIGGER IF NOT EXISTS bots_id_permanent BEFORE UPDATE OF id ON bots BEGIN
  SELECT RAISE(ABORT, 'a bot id never changes; rename the slug instead'); END;
CREATE TRIGGER IF NOT EXISTS bots_identity_insert BEFORE INSERT ON bots
WHEN EXISTS (SELECT 1 FROM bots WHERE id=NEW.slug) OR EXISTS (SELECT 1 FROM bots WHERE slug=NEW.id) BEGIN
  SELECT RAISE(ABORT, 'a bot slug cannot name another bot id, and a bot id cannot name another bot slug'); END;
CREATE TRIGGER IF NOT EXISTS bots_identity_slug BEFORE UPDATE OF slug ON bots
WHEN EXISTS (SELECT 1 FROM bots WHERE id=NEW.slug AND id<>OLD.id) BEGIN
  SELECT RAISE(ABORT, 'a bot slug cannot name another bot id'); END;
CREATE TRIGGER IF NOT EXISTS principals_human_insert AFTER INSERT ON humans BEGIN
  INSERT INTO principals(id, kind, deleted_at) VALUES('human:' || NEW.id, 'human', NULL)
  ON CONFLICT(id) DO UPDATE SET kind='human', deleted_at=NULL; END;
CREATE TRIGGER IF NOT EXISTS principals_human_delete AFTER DELETE ON humans BEGIN
  UPDATE principals SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id='human:' || OLD.id; END;
CREATE TRIGGER IF NOT EXISTS principals_bot_insert AFTER INSERT ON bots BEGIN
  INSERT INTO principals(id, kind, deleted_at) VALUES('bot:' || NEW.id, 'bot', NULL)
  ON CONFLICT(id) DO UPDATE SET kind='bot', deleted_at=NULL; END;
CREATE TRIGGER IF NOT EXISTS principals_bot_delete AFTER DELETE ON bots BEGIN
  UPDATE principals SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id='bot:' || OLD.id; END;
"""

# Actors the code writes that are not people, bots or services. Each gets a `system` principal.
SYSTEM_ACTORS = ("keeper", "system:company", "system:deploy", "system:finance", "system:gateway", "system:hosting",
                 "system:storage")


def _slug_references(conn):
    """The tables with a foreign key into bots(slug). Each moves to bots(id), with the same values."""
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return [name for name in names
            if any(fk[2] == "bots" and (fk[4] or "").lower() == "slug"
                   for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")'))]


def _as_rebuild(create, table):
    """A table's CREATE TABLE statement under its temporary name, `<table>__rebuild`."""
    statement, n = re.subn(r'^CREATE TABLE\s+(?:IF NOT EXISTS\s+)?"?' + re.escape(table) + r'"?\s*\(',
                           f'CREATE TABLE "{table}__rebuild" (', create, count=1, flags=re.I)
    if n != 1:
        raise RuntimeError(f"{table}: unexpected CREATE TABLE text; the bot id migration cannot rebuild it")
    return statement


def _rebuild(conn, table, create, columns, copied):
    """Replace `table` with `create` (its statement under the temporary name) and copy every row across. This is the
    order SQLite documents for changing a table (https://sqlite.org/lang_altertable.html). Its indexes and triggers
    are created again from their saved text, since dropping the table removes them.

    The caller turns foreign keys off, so the drop removes no rows that refer to the table, and legacy_alter_table on:
    with it off, the rename refuses while a trigger on another table names the table that is dropped for a moment."""
    kept = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL "
        "ORDER BY type, name", (table,))]
    temp = f'"{table}__rebuild"'
    conn.execute(create)
    conn.execute(f'INSERT INTO {temp} ({columns}) SELECT {copied} FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE {temp} RENAME TO "{table}"')
    for statement in kept:
        conn.execute(statement)


def up(conn):
    """The upgrade to stable bot ids.

    `bots` gains `id`, the primary key, and `slug` becomes a unique label. For every existing bot id = slug, which
    freezes the current name as its id: no stored actor string, foreign key value, history row or queued payload
    changes. Every table that referenced bots(slug) references bots(id) instead, and the principals table is filled.

    Contract: it does not BEGIN or COMMIT, so the caller owns the transaction and a failure rolls all of it back. It
    expects foreign keys to be OFF (SQLite ignores that pragma inside a transaction, so the caller sets it before
    BEGIN). It is idempotent: run on a database it has already upgraded, every step is a no-op."""
    prior = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        children = _slug_references(conn)
        # Rows that already break a foreign key are not this step's to fix; it must not add any.
        before = {name: len(conn.execute(f'PRAGMA foreign_key_check("{name}")').fetchall()) for name in children}
        if "id" not in {row[1] for row in conn.execute("PRAGMA table_info(bots)")}:
            create = _as_rebuild(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bots'")
                                 .fetchone()[0], "bots")
            create, n = re.subn(r"\bslug TEXT PRIMARY KEY\b", "id TEXT PRIMARY KEY NOT NULL, slug TEXT NOT NULL UNIQUE",
                                create, count=1)
            if n != 1:
                raise RuntimeError("bots: unexpected definition; the bot id migration cannot rebuild it")
            others = [row[1] for row in conn.execute("PRAGMA table_info(bots)") if row[1] != "slug"]
            _rebuild(conn, "bots", create, ", ".join(f'"{c}"' for c in ["id", "slug", *others]),
                     ", ".join(f'"{c}"' for c in ["slug", "slug", *others]))
        for table in children:
            create, n = re.subn(r'REFERENCES\s+"?bots"?\s*\(\s*"?slug"?\s*\)', "REFERENCES bots(id)",
                                _as_rebuild(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                                         (table,)).fetchone()[0], table), flags=re.I)
            if not n:
                raise RuntimeError(f"{table}: no reference to bots(slug) to move")
            columns = ", ".join(f'"{row[1]}"' for row in conn.execute(f'PRAGMA table_info("{table}")'))
            _rebuild(conn, table, create, columns, columns)
        for table in children:
            if any((fk[4] or "").lower() != "id" for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")')
                   if fk[2] == "bots"):
                raise RuntimeError(f"{table}: a foreign key still names bots(slug)")
            violations = len(conn.execute(f'PRAGMA foreign_key_check("{table}")').fetchall())
            if violations > before[table]:
                raise RuntimeError(f"{table}: {violations - before[table]} foreign keys do not resolve to bots(id)")
        for statement in migrations.statements(SCHEMA):
            conn.execute(statement)
        # The principals that exist before the triggers do: the people, the bots, and the system actors the code writes.
        conn.execute("INSERT INTO principals(id, kind, deleted_at) SELECT 'human:' || id, 'human', NULL FROM humans "
                     "WHERE TRUE ON CONFLICT(id) DO NOTHING")
        conn.execute("INSERT INTO principals(id, kind, deleted_at) SELECT 'bot:' || id, 'bot', NULL FROM bots "
                     "WHERE TRUE ON CONFLICT(id) DO NOTHING")
        for actor in SYSTEM_ACTORS:
            conn.execute("INSERT INTO principals(id, kind, deleted_at) VALUES(?, 'system', NULL) "
                         "ON CONFLICT(id) DO NOTHING", (actor,))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_keys'").fetchone():
            conn.execute("INSERT INTO principals(id, kind, deleted_at) SELECT 'service:' || id, 'service', NULL "
                         "FROM service_keys WHERE TRUE ON CONFLICT(id) DO NOTHING")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={prior}")
