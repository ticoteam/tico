"""Pinned threads (docs/dev/threads-contract.md §10.1): each reader's own pins, and a manager's pin for everyone.

`thread_pins` holds one row per thread and actor: `pinned` 1 pins the thread for that actor, 0 unpins it for them over a
pin for everyone. `conversations.pinned_for_everyone_at` is when a manager pinned the root for all who read it (null when
it is not). Only roots are pinned.
"""


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS thread_pins (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
                 "actor TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 1 CHECK (pinned IN (0,1)), "
                 "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (thread_id, actor))")
    conn.execute("CREATE INDEX IF NOT EXISTS thread_pins_actor ON thread_pins(actor, pinned, thread_id)")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone():
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    if "pinned_for_everyone_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN pinned_for_everyone_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS conversations_pinned_for_everyone ON conversations(pinned_for_everyone_at) "
                 "WHERE pinned_for_everyone_at IS NOT NULL")
