"""A refusal-count hold belongs to one bot's task or chat, without changing the task's status or other holds.

Released rows keep the time its refusal count started over. A task may have messages in several conversations;
a chat hold has no task and fences only that conversation.
"""


def up(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS quarantine_holds(
        id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL REFERENCES bots(id),
        task_id TEXT REFERENCES tasks(id), conversation_id TEXT REFERENCES conversations(id),
        reason TEXT NOT NULL, refusal TEXT NOT NULL, created_at TEXT NOT NULL, released_at TEXT,
        CHECK ((task_id IS NOT NULL) <> (conversation_id IS NOT NULL)))""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS quarantine_holds_task ON quarantine_holds(bot_id,task_id) "
                 "WHERE released_at IS NULL AND task_id IS NOT NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS quarantine_holds_chat ON quarantine_holds(bot_id,conversation_id) "
                 "WHERE released_at IS NULL AND conversation_id IS NOT NULL")

