-- Each agent's session in a task's one thread (backend/threads.py, session views): the agent a message belongs to,
-- NULL for a comment. Written once, when the message is (hubdb._write_message); old messages stay comments.
ALTER TABLE messages ADD COLUMN session_agent_id TEXT;
-- A session view reads one agent's messages in a thread by seq; partial, so comments add no entry.
CREATE INDEX IF NOT EXISTS messages_session_agent ON messages(conversation_id, session_agent_id, seq)
WHERE session_agent_id IS NOT NULL;
