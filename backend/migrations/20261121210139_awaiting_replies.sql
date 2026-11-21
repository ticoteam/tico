-- Who waits on an agent's reply to what their Assistant asked it for them (backend/assistant_relay.py): the server's
-- record only, never part of a message. `reply_id` is the agent's message that answered it; until then it is open, and
-- each agent message looks up the open waits on its agent in its thread tree through awaiting_replies_open.
CREATE TABLE awaiting_replies (
  message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  root_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  reply_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX awaiting_replies_open ON awaiting_replies(agent_id, root_id, thread_id) WHERE reply_id IS NULL;
