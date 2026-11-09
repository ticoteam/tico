"""Comments route like posts (backend/threads.py `plan`): nothing guesses who a message is for any more, and posting
follows nobody.

- A bot that follows a chat (any thread that is not a task's) follows it at `mentions`, so only what addresses it
  reaches it, unless it chose to follow (reason `manual`).
- A person's follow of a chat that only their own post made (reason `posted`) becomes a join (`joined`): it keeps the
  thread in their sidebar and their read mark, and no longer brings them every message there.
- Routing's queue (`thread_routings`) goes. What routing decided stays in `message_routes`, as history.
"""

TASK_ROOTS = ("SELECT s.id FROM conversations s JOIN conversations r ON r.id=COALESCE(s.root_id,s.id) "
              "WHERE r.kind='task' AND r.task_id IS NOT NULL")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def up(conn):
    tables = _tables(conn)
    if {"conversations", "thread_followers"} <= tables:
        now = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
        conn.execute(f"UPDATE thread_followers SET notify='mentions', updated_at=? WHERE actor LIKE 'bot:%' "
                     f"AND notify='live' AND reason<>'manual' AND thread_id NOT IN ({TASK_ROOTS})", (now,))
        conn.execute(f"UPDATE thread_followers SET reason='joined', updated_at=? WHERE actor LIKE 'human:%' "
                     f"AND reason='posted' AND thread_id NOT IN ({TASK_ROOTS})", (now,))
    if "thread_routings" in tables:
        conn.execute("DROP TABLE thread_routings")
