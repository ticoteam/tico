"""Seek each pool's oldest waits without sorting or scanning its backlog.

The waits table normally starts in Store.initialize, after migrations, so create it here on a fresh database too.
"""


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS check_waits("
                 "id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, step_id TEXT NOT NULL, title TEXT NOT NULL, "
                 "heads TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'waiting', "
                 "child_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempt_id TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS check_waits_retry ON check_waits(step_id,state,created_at,id)")
