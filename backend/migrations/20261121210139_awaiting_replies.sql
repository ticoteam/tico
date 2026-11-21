-- Who waits on an agent's reply to what their Assistant asked it for them (backend/assistant_relay.py): the server's
-- record only, never part of a message. The agent's first message in that thread, or one under it, deletes the wait
-- as it answers it; each agent message looks up its waits through awaiting_replies_agent.
CREATE TABLE awaiting_replies (
  message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  root_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX awaiting_replies_agent ON awaiting_replies(agent_id, root_id, thread_id);
