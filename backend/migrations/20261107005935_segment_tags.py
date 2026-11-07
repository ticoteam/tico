"""Segment tags on goals and tasks (backend/segment_tags.py): one optional segment each, NULL or '' for none.

A goal or task filed before this has none, and so does every row a fresh install starts with. The partial indexes
cover only the rows that have a segment, which are the only rows a segment filter reads.
"""


def up(conn):
    for table in ("goals", "tasks"):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "segment_id" not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN segment_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS goals_segment ON goals(segment_id) WHERE segment_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_segment ON tasks(segment_id) WHERE segment_id IS NOT NULL")
