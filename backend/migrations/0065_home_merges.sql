-- Everyone's old personal chat with a bot moves into that bot's Home (backend/bot_home_merge.py, owner decision Nov 3).
-- These two tables are the record of that move: which room went into which Home, and which messages it carried, so the
-- move can be read back (a write to an old room lands in the Home, `bot_home_merge.home_for`) and undone by hand.
CREATE TABLE IF NOT EXISTS home_merges(
  conversation_id TEXT PRIMARY KEY,
  home_id TEXT NOT NULL,
  bot_id TEXT NOT NULL,
  owner_id TEXT,
  room_key TEXT,
  messages_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS home_merges_home ON home_merges(home_id);
CREATE TABLE IF NOT EXISTS home_merge_messages(
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS home_merge_messages_conversation ON home_merge_messages(conversation_id);
