"""Pinned threads (docs/dev/threads-contract.md §10.1): each reader's own pins, and a manager's pin for everyone.

`thread_pins` holds one row per thread and actor: `pinned` 1 pins the thread for that actor, 0 unpins it for them over a
pin for everyone. `conversations.pinned_for_everyone_at` is when a manager pinned the root for all who read it (null when
it is not). Only roots are pinned.

`org_rule_holds_reviewer` lets Catch up read only the holds a reader reviews (thread_rail._holds). The table is
backend/org_rules.py's; on a new hub the migrations run before it, so this makes it with that same statement first.
"""

HOLDS = ("CREATE TABLE IF NOT EXISTS org_rule_holds(id TEXT PRIMARY KEY, operation TEXT NOT NULL, payload TEXT NOT NULL, "
         "author TEXT NOT NULL, destinations TEXT NOT NULL DEFAULT '[]', rule_ids TEXT NOT NULL DEFAULT '[]', "
         "verdict TEXT NOT NULL DEFAULT '{}', why TEXT NOT NULL DEFAULT '', nonce TEXT NOT NULL, "
         "state TEXT NOT NULL CHECK (state IN ('held','sent','kept')), reviewer TEXT, task_id TEXT, "
         "created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT)")


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS thread_pins (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
                 "actor TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 1 CHECK (pinned IN (0,1)), "
                 "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (thread_id, actor))")
    conn.execute("CREATE INDEX IF NOT EXISTS thread_pins_actor ON thread_pins(actor, pinned, thread_id)")
    conn.execute(HOLDS)
    conn.execute("CREATE INDEX IF NOT EXISTS org_rule_holds_reviewer ON org_rule_holds(reviewer, state, created_at) "
                 "WHERE reviewer IS NOT NULL")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone():
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    if "pinned_for_everyone_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN pinned_for_everyone_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS conversations_pinned_for_everyone ON conversations(pinned_for_everyone_at) "
                 "WHERE pinned_for_everyone_at IS NOT NULL")
    # The rail's title order over open roots (thread_rail._TITLE_ORDER, unnamed last), so a page reads in order.
    conn.execute("CREATE INDEX IF NOT EXISTS conversations_open_title ON conversations("
                 "CASE WHEN TRIM(COALESCE(subject,''))='' THEN 1 ELSE 0 END, lower(COALESCE(subject,'')), id) "
                 "WHERE parent_id IS NULL AND archived_at IS NULL AND closed_at IS NULL")
