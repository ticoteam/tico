"""Stage rules (backend/stage_rules.py): each stage says what it enforces, as When -> If -> Then rows in their own
table. The default rules are seeded at the next start (pipeline_settings.ensure_presets -> stage_rules.ensure_defaults),
not here, so a company that retires one keeps it retired. `then_do` is the rule's Then (THEN is an SQL word);
stage_rules.view names it `then`."""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS stage_rules ("
    "id TEXT PRIMARY KEY, name TEXT NOT NULL, pipeline_id TEXT NOT NULL DEFAULT '', stages TEXT NOT NULL DEFAULT '[]', "
    "event TEXT NOT NULL, timer TEXT NOT NULL DEFAULT '{}', cond TEXT NOT NULL DEFAULT '{}', "
    "jev TEXT NOT NULL DEFAULT '', then_do TEXT NOT NULL, then_params TEXT NOT NULL DEFAULT '{}', "
    "reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suggested')), "
    "suggests TEXT NOT NULL DEFAULT '', builtin TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, retired_at TEXT, revision INTEGER NOT NULL DEFAULT 1)",
    "CREATE INDEX IF NOT EXISTS stage_rules_on ON stage_rules(pipeline_id, status, retired_at)",
)


def up(conn):
    for statement in SCHEMA:
        conn.execute(statement)
