"""Tico's notices to bots leave people's chats: a chat holds only people and bots talking.

Tico steered a bot by writing a notice from the keeper into the room it works in (a task's turn and what is missing,
the never-stuck ladder's "You stopped before this task was finished", a health wake, a stage reminder), and the bot's
run posted its answer to that notice as an ordinary message, shown as a reply to a message nobody could see. What Tico
tells a bot now reaches it only through its run: the notice is the message the run starts from (jobs.message_id) and
stays in the bot's own reads of the room, but it has no seq, so no timeline, unread count, Flow chat, task page or live
stream a person reads shows it. Its words stay one tap away on the run that acted on it (the task's Runs).

1. The stamp trigger (threads_messages_stamp, written by the baseline) gives a message from the keeper to a bot no seq
   and leaves the thread's counters alone, as it already did for a carrier.
2. The notices already written leave every timeline the same way: their seq is cleared. Found by the keeper's own
   messages (messages_from_kind).
3. A bot's bare answer to one leaves with it: the text its run posted in reply (refs.turn_id), to a notice that is not
   its task's request (refs.news "assignment") and that nobody's act caused, naming nobody, in a run that had no
   person's message folded in and did not finish its task. Its seq is cleared and a delivery of it still waiting is
   moot; its text stays on the run (attempts.final_text). The answer to a task's request or to a person's own act, one
   that @mentions someone, one with a person's words in its run, the one that finished the task, and anything the bot
   sent itself stay as they are.
4. A thread whose newest message, or newest unread one, left has `last_seq` or `last_unread_seq` lowered to the newest
   message left, so it does not show as unread with nothing new in it.

Running this again changes nothing.
"""

from datetime import datetime, timezone

STAMP = (
    "CREATE TRIGGER threads_messages_stamp AFTER INSERT ON messages "
    "WHEN NOT COALESCE(json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.carries'),'')<>'' "
    "AND NOT (NEW.sender_id='keeper' AND COALESCE(NEW.recipient_id,'') LIKE 'bot:%') "
    "BEGIN INSERT OR IGNORE INTO sequences(name,value) VALUES('messages',0);"
    "UPDATE sequences SET value=value+1 WHERE name='messages';"
    "UPDATE messages SET seq=(SELECT value FROM sequences WHERE name='messages'), "
    "origin_id=CASE WHEN (NEW.origin_id IS NULL OR EXISTS (SELECT 1 FROM messages o WHERE o.origin_id=NEW.origin_id "
    "AND o.id<>NEW.id)) THEN lower(hex(randomblob(16)))||'@'||COALESCE((SELECT json_extract(value,'$.install_id') "
    "FROM registry_metadata WHERE key='usage-count'),'local') ELSE NEW.origin_id END, "
    "restricted=CASE WHEN (NEW.origin_id IS NULL OR EXISTS (SELECT 1 FROM messages o WHERE o.origin_id=NEW.origin_id "
    "AND o.id<>NEW.id)) THEN 1 ELSE NEW.restricted END WHERE id=NEW.id;"
    "UPDATE conversations SET last_seq=(SELECT value FROM sequences WHERE name='messages'), "
    "last_unread_seq=CASE WHEN (NEW.sender_id LIKE 'human:%' OR COALESCE(NEW.recipient_id,'')<>'') "
    "AND NOT (json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.import') IS NOT NULL "
    "OR json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.replay') IS NOT NULL) "
    "THEN (SELECT value FROM sequences WHERE name='messages') ELSE last_unread_seq END "
    "WHERE id=NEW.conversation_id; END"
)

NOTICES = (
    "SELECT id, conversation_id, seq FROM messages WHERE sender_id = 'keeper' AND seq IS NOT NULL "
    "AND coalesce(recipient_id, '') LIKE 'bot:%'"
)
# A bot's run answering a keeper notice to it (in_reply_to, by messages_reply_kind), posted by the completion
# (refs.turn_id), with nothing that makes it a person's: their act behind the notice, an @mention, their words folded
# into the run, or the run finishing the task.
ANSWERS = (
    "SELECT m.id, m.conversation_id, m.seq FROM messages n JOIN messages m ON m.in_reply_to = n.id "
    "WHERE n.sender_id = 'keeper' AND coalesce(n.recipient_id, '') LIKE 'bot:%' AND m.sender_id = n.recipient_id "
    "AND m.seq IS NOT NULL AND json_valid(m.refs) AND json_extract(m.refs, '$.turn_id') IS NOT NULL "
    "AND NOT (json_valid(n.refs) AND (coalesce(json_extract(n.refs, '$.caused_by'), '') LIKE 'human:%' "
    "OR coalesce(json_extract(n.refs, '$.news'), '') = 'assignment')) "
    "AND NOT EXISTS (SELECT 1 FROM message_mentions x WHERE x.message_id = m.id) "
    "AND NOT EXISTS (SELECT 1 FROM attempt_inputs i JOIN messages p ON p.id = i.message_id "
    "WHERE i.attempt_id = json_extract(m.refs, '$.turn_id') AND (p.sender_id LIKE 'human:%' "
    "OR (json_valid(p.refs) AND coalesce(json_extract(p.refs, '$.caused_by'), '') LIKE 'human:%'))) "
    "AND NOT EXISTS (SELECT 1 FROM attempts a JOIN tasks t ON t.id = json_extract(m.refs, '$.task') "
    "WHERE a.id = json_extract(m.refs, '$.turn_id') "
    "AND max(coalesce(t.done_at, ''), coalesce(t.closed_at, '')) BETWEEN coalesce(a.started_at, a.created_at) "
    "AND m.created_at)"
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


def _take_out(conn, rows, hidden, moot):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    for message_id, thread_id, seq in rows:
        conn.execute("UPDATE messages SET seq = NULL WHERE id = ?", (message_id,))
        if moot:
            conn.execute(
                "UPDATE message_deliveries SET state = 'moot', updated_at = ? WHERE message_id = ? "
                "AND recipient NOT LIKE 'bot:%' AND state IN ('pending', 'delivered', 'capped', 'held')",
                (now, message_id),
            )
        hidden.setdefault(thread_id, set()).add(int(seq))


def _counters(conn, hidden):
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
    conn.execute("DROP TRIGGER IF EXISTS threads_messages_stamp")
    conn.execute(STAMP)
    # A new file has no notices, and some of the tables the answers are read through are made by module code after
    # the migrations.
    needed = ("messages", "conversations", "message_mentions", "message_deliveries", "attempts", "attempt_inputs")
    if not all(_table(conn, name) for name in (*needed, "tasks")):
        return
    hidden = {}  # thread id -> the seqs taken out of it
    _take_out(conn, conn.execute(ANSWERS).fetchall(), hidden, moot=True)
    _take_out(conn, conn.execute(NOTICES).fetchall(), hidden, moot=False)
    _counters(conn, hidden)
