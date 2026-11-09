"""Native goals belong to a conversation, independently of company goals."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_goals (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 bot TEXT NOT NULL REFERENCES bots(slug), objective TEXT NOT NULL CHECK(length(objective) BETWEEN 1 AND 4000),
 status TEXT NOT NULL CHECK(status IN ('active','paused','met','stopped','cleared')),
 note TEXT NOT NULL DEFAULT '', set_by TEXT NOT NULL, set_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, ended_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS chat_goals_current ON chat_goals(conversation_id);
CREATE INDEX IF NOT EXISTS chat_goals_bot_status ON chat_goals(bot,status);
"""
