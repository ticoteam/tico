"""Asks are gone (threads + tasks, step 6c): nothing writes an `ask` or `answer` message any more.

Each open ask (unanswered, not superseded, not deleted, with no answer replying to it) is copied into `asks_moved`
here, and the server's next start turns each into what the code now uses (question_tasks.move_asks): an ask to a person
on a live task becomes a question subtask for them (or joins the open question card they already have there), a loose
ask to a person becomes a question task of its own, and an ask to a bot becomes a DM between the two, flagged
needs-action. Answered and stale asks stay as they are, readable in their threads. A migration never calls app code,
so making those tasks and messages waits for that start.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not {"kind", "answered_by_id", "superseded_at", "deleted_at", "in_reply_to"} <= _columns(conn, "messages"):
        return                      # a new file: its messages table comes later, and has no asks
    staged = ("FROM messages m WHERE m.kind='ask' AND m.deleted_at IS NULL AND m.answered_by_id IS NULL "
              "AND m.superseded_at IS NULL AND NOT EXISTS (SELECT 1 FROM messages a WHERE a.in_reply_to=m.id "
              "AND a.kind='answer' AND a.deleted_at IS NULL)")
    # Only a file with asks to move gets the staging table: move_asks drops it once they are moved.
    if conn.execute(f"SELECT 1 {staged} LIMIT 1").fetchone():
        conn.execute("CREATE TABLE IF NOT EXISTS asks_moved(message_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        conn.execute(f"INSERT OR IGNORE INTO asks_moved(message_id, created_at) SELECT m.id, m.created_at {staged}")
