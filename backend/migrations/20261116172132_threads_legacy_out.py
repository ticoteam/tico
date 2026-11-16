"""The message indexes, which the one-time threads migration (threads.migrate, gone) built after its backfill.

Every database this release opens already ran that migration, which built them, so on those this changes nothing; a new
database gets them here. Migration 0047 makes them only on a file whose backfill had run.
"""

INDEXES = (
    # Not unique: an insert copying another row's seq or origin must not fail before the stamp trigger replaces them.
    # The sequence makes seqs unique; message_revisions makes (origin_id, revision) unique.
    "CREATE INDEX IF NOT EXISTS messages_seq ON messages(seq) WHERE seq IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_origin ON messages(origin_id) WHERE origin_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_thread_seq ON messages(conversation_id, seq)",
    # The people's messages of a thread: the sidebar's unread rank skips a long run of bot posts past someone's mark.
    "CREATE INDEX IF NOT EXISTS messages_thread_human ON messages(conversation_id, seq) WHERE sender_id LIKE 'human:%'",
    # A migrated Home's shared-room history: which Homes still hold messages read by their old readers.
    "CREATE INDEX IF NOT EXISTS messages_legacy_audience ON messages(conversation_id, legacy_audience) "
    "WHERE legacy_audience IS NOT NULL",
)


def up(conn):
    for statement in INDEXES:
        conn.execute(statement)
