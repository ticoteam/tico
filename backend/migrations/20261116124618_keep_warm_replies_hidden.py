"""Keep-warm replies already posted leave every timeline.

A keep-warm turn (removed: 20261116130940_keep_warm_removed) asked a task's session for a one-word "ok". Its notice
was always kept out of every timeline (no seq), but its reply was posted to the task's room as an ordinary bot message,
shown as an answer to a message nobody could see: one every 46 minutes or so, burying the bot's real questions. This
takes the ones already written out the same way the notice is kept out: their seq is cleared, so no timeline, unread
count, Flow chat, task page or later turn's room history reads them. Their text stays on the run (attempts.final_text),
and a delivery of one still waiting is moot.

A thread whose newest message, or newest unread one, was such a reply has `last_seq` or `last_unread_seq` lowered to
the newest message left, so it does not show as unread with nothing new in it.

Found from the keep-warm runs (attempts.purpose, created since keep-warm shipped: the attempts_created index), through
their job's notice (jobs' primary key), to the bot's replies to it (messages_reply_kind index), each checked by the run
it names (refs.run.attempt_id). Running it again changes nothing.
"""

from datetime import datetime, timezone

KEEP_WARM = "keep warm"  # run_triggers.KEEP_WARM, copied so the migration stands alone
SHIPPED = "2026-11-13"  # migration 20261113235427_keep_warm_hours: no keep-warm run is older

FOUND = (
    "SELECT m.id, m.conversation_id, m.seq FROM attempts a JOIN jobs j ON j.id = a.job_id "
    "JOIN messages m ON m.in_reply_to = j.message_id "
    "WHERE a.created_at >= ? AND a.purpose = ? AND m.seq IS NOT NULL AND m.sender_id = 'bot:' || a.bot_id "
    "AND json_valid(m.refs) AND json_extract(m.refs, '$.run.attempt_id') = a.id"
)
# The newest message left in a thread that stamps its unread counter (threads_messages_stamp, threads.record).
NEWEST_UNREAD = (
    "SELECT coalesce(max(m.seq), 0) FROM messages m WHERE m.conversation_id = ? AND m.seq IS NOT NULL "
    "AND (m.sender_id LIKE 'human:%' OR coalesce(m.recipient_id, '') <> '' OR EXISTS (SELECT 1 FROM message_mentions x "
    "WHERE x.message_id = m.id AND x.in_audience = 1)) "
    "AND NOT (json_valid(m.refs) AND (json_extract(m.refs, '$.import') IS NOT NULL "
    "OR json_extract(m.refs, '$.replay') IS NOT NULL))"
)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    if not all(_table(conn, name) for name in ("attempts", "jobs", "messages", "conversations", "message_mentions")):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    hidden = {}  # thread id -> the seqs taken out of it
    for message_id, thread_id, seq in conn.execute(FOUND, (SHIPPED, KEEP_WARM)).fetchall():
        conn.execute("UPDATE messages SET seq = NULL WHERE id = ?", (message_id,))
        if _table(conn, "message_deliveries"):
            conn.execute(
                "UPDATE message_deliveries SET state = 'moot', updated_at = ? WHERE message_id = ? "
                "AND state IN ('pending', 'delivered', 'capped', 'held')",
                (now, message_id),
            )
        hidden.setdefault(thread_id, set()).add(int(seq))
    for thread_id, seqs in hidden.items():
        found = conn.execute(
            "SELECT last_seq, last_unread_seq FROM conversations WHERE id = ?", (thread_id,)
        ).fetchone()
        if not found:
            continue
        last, unread = found
        if last in seqs:
            newest = conn.execute(
                "SELECT coalesce(max(seq), 0) FROM messages WHERE conversation_id = ? AND seq IS NOT NULL",
                (thread_id,),
            ).fetchone()[0]
            conn.execute("UPDATE conversations SET last_seq = ? WHERE id = ?", (newest, thread_id))
        if unread in seqs:
            newest = conn.execute(NEWEST_UNREAD, (thread_id,)).fetchone()[0]
            conn.execute("UPDATE conversations SET last_unread_seq = ? WHERE id = ?", (newest, thread_id))
