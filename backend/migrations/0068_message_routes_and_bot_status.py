"""Who routing asked for a message, and the reads behind a thread's bot status (docs/dev/threads-contract.md §4.10).

`message_routes` holds one row per person's message that went through routing (backend/thread_routing.py): `bot_ids` is
the JSON list of bot actors the decision model asked, `[]` when it asked none (it picked nobody, could not run, or gave
up). The timeline's `routed` reads it; a message routing never looked at has no row.

`message_deliveries_open_bots` holds exactly the open wakes a person's message gave bots (queue or steer, not yet read),
by root: the status rows under a chat read them (backend/thread_bot_status.py), and so do the triggers that tell a
root's readers the rows changed.

`changes_bot_status` finds a root's latest `bot_status` change, so a run's steps send at most one every five seconds
(backend/events.py). On a new file the change log does not exist yet when the migrations run: it is made here with
backend/events.py's own statement.
"""

CHANGES = ("CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, "
           "kind TEXT, subject_id TEXT, ref TEXT, conversation_id TEXT, bot TEXT, actor TEXT, created_at TEXT NOT NULL)")


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS message_routes (message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
                 "bot_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute(CHANGES)
    conn.execute("CREATE INDEX IF NOT EXISTS changes_bot_status ON changes(subject_id, created_at) "
                 "WHERE topic='threads' AND kind='bot_status'")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_deliveries'").fetchone():
        conn.execute("CREATE INDEX IF NOT EXISTS message_deliveries_open_bots ON message_deliveries(root_id, created_at) "
                     "WHERE recipient LIKE 'bot:%' AND mode IN ('queue','steer') "
                     "AND state IN ('pending','delivered','capped') AND cause='human'")
