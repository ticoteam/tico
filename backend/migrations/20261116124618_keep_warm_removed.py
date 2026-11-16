"""Keep-warm is gone: what its turns left behind, cleared in this order.

A keep-warm turn was a one-line turn Tico sent an idle task session to hold its prompt cache: a keeper notice with no
seq (in no timeline), `refs.wake` `keep_warm`, and a run with purpose "keep warm". It went: few were followed by real
work within the hour, and the bot's "ok" was posted to the task's room as an ordinary message, shown as an answer to
a message nobody could see, one every 46 minutes or so, burying the bot's real questions.

1. The replies already posted leave every timeline the way the notice was kept out: their seq is cleared, so no
   timeline, unread count, Flow chat, task page or later turn's room history reads them. Their text stays on the run
   (attempts.final_text), and a delivery of one still waiting is moot. A thread whose newest message, or newest unread
   one, was such a reply has `last_seq` or `last_unread_seq` lowered to the newest message left, so it does not show
   as unread with nothing new in it. Found from the keep-warm runs (the attempts_created index), through their job's
   notice (jobs' primary key), to the bot's replies to it (messages_reply_kind index), each checked by the run it
   names (refs.run.attempt_id).
2. A keep-warm job still queued is canceled: run as an ordinary turn it would answer "ok" in the task's room. One
   already leased or running finishes, and execution.complete posts nothing for it.
3. Its finished runs stay as history, but no longer on their task: their task_id, message_id and job_id are cleared.
   They were never runs on the task, and the readers that left them out (the never-stuck ladder's run count, the last
   run on a task, the checks a run already saw, a result's run) now count every run linked to it. After step 1, which
   finds the replies through job_id.

`stage_keep_warm` (emptied) and `pipelines.keep_warm_hours` stay, and nothing reads or writes them. Dropping them would not give
the same schema in every order: on an install where 20261113235427_keep_warm_hours is also pending it may run after
this file and make them again, and a file that has run is never edited (docs/releasing.md, Migrations).

No keep-warm run is older than that migration's day (SHIPPED). Running this again changes nothing.
"""

from datetime import datetime, timezone

KEEP_WARM = "keep warm"  # the purpose its runs carried
SHIPPED = "2026-11-13"  # migration 20261113235427_keep_warm_hours: no keep-warm run is older

REPLIES = (
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


def _hide_replies(conn):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    hidden = {}  # thread id -> the seqs taken out of it
    for message_id, thread_id, seq in conn.execute(REPLIES, (SHIPPED, KEEP_WARM)).fetchall():
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


def up(conn):
    # Its rows go first, before any early return: each names a stage (REFERENCES stages(id), no cascade), and nothing
    # deletes them with their stage any more, so one left behind would stop that stage from being deleted. Where the
    # table is made after this file, it starts empty.
    if _table(conn, "stage_keep_warm"):
        conn.execute("DELETE FROM stage_keep_warm")
    # A new file has no runs, and its attempts table's triggers name tables the module code makes after the migrations.
    needed = ("attempts", "jobs", "messages", "conversations", "message_mentions")
    if not all(_table(conn, name) for name in needed):
        return
    _hide_replies(conn)
    conn.execute(
        "UPDATE jobs SET state = 'canceled' WHERE state = 'queued' AND message_id IN (SELECT m.id FROM jobs j "
        "JOIN messages m ON m.id = j.message_id WHERE j.state = 'queued' AND m.sender_id = 'keeper' "
        "AND json_valid(m.refs) AND json_extract(m.refs, '$.wake') = 'keep_warm')"
    )
    conn.execute(
        "UPDATE attempts SET task_id = NULL, message_id = NULL, job_id = NULL WHERE created_at >= ? "
        "AND purpose = ? AND coalesce(state, '') NOT IN ('leased', 'running') "
        "AND (task_id IS NOT NULL OR message_id IS NOT NULL OR job_id IS NOT NULL)",
        (SHIPPED, KEEP_WARM),
    )
