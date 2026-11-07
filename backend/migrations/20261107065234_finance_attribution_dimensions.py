"""Attribution rules by class, location and project (backend/finance/store/attribution.py): a rule's match kind widens
from vendor, P&L account and money account to those plus class, location and project. SQLite cannot widen a CHECK in
place, so the table is rebuilt keeping its rows, and its indexes are made again on the new table. No table refers to
fin_attribution_rules, so nothing else needs rebuilding. Idempotent: a table that already allows the kinds is left alone.
"""

_COLUMNS = "id, book_id, name, match_kind, match_id, splits, enabled, apply_from, created_by, created_at, updated_at, deleted_at"

_TABLE = """CREATE TABLE {name} (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 name TEXT NOT NULL,
 match_kind TEXT NOT NULL CHECK (match_kind IN ('vendor','account','money_account','class','location','project')),
 match_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 enabled INTEGER NOT NULL DEFAULT 1,
 apply_from TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT,
 deleted_at TEXT
)"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS fin_attribution_rules_book ON fin_attribution_rules(book_id, deleted_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS fin_attribution_rules_match ON fin_attribution_rules(book_id, match_kind, match_id) "
    "WHERE deleted_at IS NULL",
)


def up(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fin_attribution_rules'").fetchone()
    if row is None or "'project'" in str(row[0]):
        return
    # The rename re-parses every trigger, and an earlier migration's trigger names a table the module code makes after
    # the migrations (presences_meeting_delete names task_roles), so the triggers step aside for the rebuild.
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    for name, _ in triggers:
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    conn.execute(_TABLE.format(name="fin_attribution_rules_new"))
    conn.execute(f"INSERT INTO fin_attribution_rules_new({_COLUMNS}) SELECT {_COLUMNS} FROM fin_attribution_rules")
    conn.execute("DROP TABLE fin_attribution_rules")  # its indexes go with it
    conn.execute("ALTER TABLE fin_attribution_rules_new RENAME TO fin_attribution_rules")
    for statement in _INDEXES:
        conn.execute(statement)
    for _, sql in triggers:
        conn.execute(sql)
