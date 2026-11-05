-- A broad SQL count reads only the privacy columns. Keep them together in room order so it does not fetch
-- every historical message body through messages_room_rowid. Deleted messages never enter the SQL view.
CREATE INDEX IF NOT EXISTS messages_sql_visibility
ON messages(conversation_id, deleted_at, id, refs, in_reply_to, legacy_audience)
WHERE deleted_at IS NULL;
