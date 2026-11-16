"""What the pre-0.6 thread history left behind, now that the code that read it is gone.

Every database this release opens was made on 0.6 or upgraded from a 0.5 one with no tasks (migrations.refuse_old_tasks),
so it already ran the one-time threads migration and the move of each person's bot chat into the bot's Home, and neither
runs again. What they left:

1. Shared-room history moved into a Home kept its old readers (`messages.legacy_audience`, read live). Only a database
   made before native threads holds any. Rather than show it to everyone who reads the Home now, each such message is
   deleted as a person's delete deletes one: its words cleared, its mentions, reactions and routes gone, nothing still
   waiting for it delivered. The column stays, always empty: each claim still hands it to the runner as null.
2. The message indexes, which that migration built after its backfill, are made here; its index of that history goes.
3. The record of which chats moved into a Home (`home_merges`, `home_merge_messages`) goes: nothing reads it.
"""

from datetime import datetime, timezone

INDEXES = (
    # Not unique: an insert copying another row's seq or origin must not fail before the stamp trigger replaces them.
    # The sequence makes seqs unique; message_revisions makes (origin_id, revision) unique.
    "CREATE INDEX IF NOT EXISTS messages_seq ON messages(seq) WHERE seq IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_origin ON messages(origin_id) WHERE origin_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_thread_seq ON messages(conversation_id, seq)",
    # The people's messages of a thread: the sidebar's unread rank skips a long run of bot posts past someone's mark.
    "CREATE INDEX IF NOT EXISTS messages_thread_human ON messages(conversation_id, seq) WHERE sender_id LIKE 'human:%'",
)
GONE = "SELECT id FROM messages WHERE legacy_audience IS NOT NULL"


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "legacy_audience" in columns:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        for table in ("message_mentions", "message_reactions", "message_routes"):
            if table in tables:
                conn.execute(f"DELETE FROM {table} WHERE message_id IN ({GONE})")
        if "message_revisions" in tables:
            conn.execute(f"UPDATE message_revisions SET body=NULL WHERE message_id IN ({GONE})")
        if "message_deliveries" in tables:
            conn.execute(
                f"UPDATE message_deliveries SET state='moot', updated_at=? WHERE message_id IN ({GONE}) "
                "AND state IN ('pending','capped','held')",
                (now,),
            )
        conn.execute(
            "UPDATE messages SET body='', deleted_at=COALESCE(deleted_at,?), legacy_audience=NULL "
            "WHERE legacy_audience IS NOT NULL",
            (now,),
        )
    conn.execute("DROP INDEX IF EXISTS messages_legacy_audience")
    for statement in INDEXES:
        conn.execute(statement)
    conn.execute("DROP TABLE IF EXISTS home_merge_messages")
    conn.execute("DROP TABLE IF EXISTS home_merges")
