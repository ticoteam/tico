"""A noisy check's finding waits for its confirmation run: health_findings gets `pending` (backend/maintenance_steps/check_health.py).

`pending` is 1 on a finding a noisy check saw once and has not filed yet, and 0 once it is filed or cleared. A new file gets
the column from maintenance_health.SCHEMA, which creates the table in this shape. A file that already has the table gets
it here. A file without the table skips it: its module's CREATE makes the table, with the column. Running it again finds
the column and changes nothing.
"""


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "health_findings" not in tables:
        return
    columns = {row[1] for row in conn.execute('PRAGMA table_info("health_findings")')}
    if "pending" not in columns:
        conn.execute("ALTER TABLE health_findings ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
