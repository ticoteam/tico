"""Deleted threads (Archive a thread where you see it, criteria 3 and A4/A5).

`conversations.deleted_at` is when a manager deleted a thread: every message in it was already deleted and nobody else
was in it. A deleted thread is also archived, so it leaves every list, and it reads as not found.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone():
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN deleted_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS conversations_deleted ON conversations(deleted_at) WHERE deleted_at IS NOT NULL"
    )
