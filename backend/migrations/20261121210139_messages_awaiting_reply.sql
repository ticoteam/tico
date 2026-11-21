-- An agent's open "awaiting reply" markers (backend/assistant_relay.py): each agent message looks up the markers for
-- its agent in its thread tree, so the lookup reads only the open markers, never the thread's history.
CREATE INDEX IF NOT EXISTS messages_awaiting_reply ON messages(
    json_extract(refs, '$.awaiting_reply.agent'),
    json_extract(refs, '$.awaiting_reply.root')
) WHERE json_extract(refs, '$.awaiting_reply.agent') IS NOT NULL
    AND json_extract(refs, '$.awaiting_reply.reply_id') IS NULL;
