-- An agent's open "awaiting reply" markers (backend/assistant_relay.py). The marker is the server's alone from now on:
-- any copy a writer stored before is cleared first, so none of them can match. Then each agent message looks up the open
-- markers for its agent in its thread tree through this index, never reading the thread's history.
UPDATE messages SET refs=json_remove(refs, '$.awaiting_reply')
WHERE json_valid(refs) AND json_extract(refs, '$.awaiting_reply') IS NOT NULL;
CREATE INDEX IF NOT EXISTS messages_awaiting_reply ON messages(
    json_extract(refs, '$.awaiting_reply.agent'),
    json_extract(refs, '$.awaiting_reply.root')
) WHERE json_extract(refs, '$.awaiting_reply.agent') IS NOT NULL
    AND json_extract(refs, '$.awaiting_reply.reply_id') IS NULL;
