"""A matter's own dates (backend/risk.py): the earliest it may be done and when it is due.

- `matters.starts_at`: not before. The earliest the matter's step may be done ("records may be produced no earlier than
  November 19, 10 AM"); never a deadline.
- `matters.due_at`: due by. The deadline.
- `matters.dates_label`: what the dates are for, in a few words ("Produce records").

Each date is a day (YYYY-MM-DD, the whole day) or a UTC time (YYYY-MM-DDTHH:MM:SSZ), or null. Every column is added
with ALTER TABLE ... ADD COLUMN; no table is rebuilt. Pending migrations run in any order, so on a company where
20261119025516_risks_and_matters has not run yet this makes `matters` as that one does (its CREATE TABLE IF NOT EXISTS
then leaves it be). Running it again changes nothing.
"""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS matters ("
        "task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, "
        "kind TEXT NOT NULL CHECK (kind IN ('dispute','request','notice','incident')), "
        "party_entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL, "
        "amount INTEGER, currency TEXT, claim TEXT NOT NULL DEFAULT '', "
        "side TEXT NOT NULL DEFAULT 'theirs' CHECK (side IN ('theirs','ours')), "
        "forum TEXT NOT NULL DEFAULT '', "
        "place_id TEXT REFERENCES entities(id) ON DELETE SET NULL, "
        "product_id TEXT REFERENCES entities(id) ON DELETE SET NULL, "
        "version INTEGER NOT NULL DEFAULT 1, "
        "created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_by_id TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS matters_kind ON matters(kind)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(matters)")}
    for column, ddl in (
        ("starts_at", "TEXT"),
        ("due_at", "TEXT"),
        ("dates_label", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in columns:
            conn.execute(f"ALTER TABLE matters ADD COLUMN {column} {ddl}")
