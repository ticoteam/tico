"""Task followers (backend/task_followers.py): each open task's owner follows its own thread, and bots that follow a
task's thread only because they posted in it stop following. People's follows stay as they are. A row that stops
following keeps its read mark."""

ACTIVE = ("open", "doing", "waiting", "review", "ready")
# A task's thread and the sub-threads under it.
TASK_THREADS = ("SELECT s.id FROM conversations s JOIN conversations r ON r.id=COALESCE(s.root_id,s.id) "
                "WHERE r.kind='task' AND r.task_id IS NOT NULL")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def up(conn):
    if not {"tasks", "conversations", "thread_followers"} <= _tables(conn):
        return
    now = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
    conn.execute(
        "INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,updated_at) "
        "SELECT lower(hex(randomblob(16))), t.conversation_id, t.owner_id, 1, 'live', 'owner', 0, ?, ? "
        "FROM tasks t JOIN conversations cv ON cv.id=t.conversation_id AND cv.task_id=t.id "
        f"WHERE t.status IN ({','.join('?' * len(ACTIVE))}) AND (t.owner_id LIKE 'human:%' OR t.owner_id LIKE 'bot:%') "
        "ON CONFLICT(thread_id,actor) DO UPDATE SET following=1, reason='owner', updated_at=excluded.updated_at "
        "WHERE following=0 OR reason IN ('posted','joined','read')", (now, now, *ACTIVE))
    conn.execute(f"UPDATE thread_followers SET following=0, reason='read', updated_at=? WHERE following=1 "
                 f"AND reason='posted' AND actor LIKE 'bot:%' AND thread_id IN ({TASK_THREADS})", (now,))
