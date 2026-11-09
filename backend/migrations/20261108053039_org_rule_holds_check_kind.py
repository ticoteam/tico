"""Structured holds: an org_rule_holds row says which check held it.

`judged` is the rule check that asks the engine (backend/org_rule_check.py, as it always has); `structured` is the
Directory's own rules (backend/entities/rules.py), which hold a write on the entities it names with no engine call. Both
kinds sit on the same held path: the same review, Send anyway and listings. Existing holds were all judged, so the
default is right for every row already written.

The table is created by 0063_thread_pins and by backend/org_rules.py's SCHEMA (which carries the column for a new hub);
a hub where the table does not exist yet has nothing to alter, so this migration skips it.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='org_rule_holds'").fetchone():
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(org_rule_holds)")}
    if "check_kind" not in columns:
        conn.execute("ALTER TABLE org_rule_holds ADD COLUMN check_kind TEXT NOT NULL DEFAULT 'judged'")
