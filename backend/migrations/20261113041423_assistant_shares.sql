-- The bots a person lets read their own Assistant chat, read only (backend/assistant_share.py). Only that person adds
-- or removes a row; a bot reads the chat only while its row is here.
CREATE TABLE IF NOT EXISTS assistant_shares (
  owner TEXT NOT NULL, bot TEXT NOT NULL, created_at TEXT NOT NULL, last_read_at TEXT,
  PRIMARY KEY (owner, bot));
CREATE INDEX IF NOT EXISTS assistant_shares_bot ON assistant_shares(bot);
-- Whether those bots also read what the person's Assistant has learned about them; off unless the person turns it on.
CREATE TABLE IF NOT EXISTS assistant_share_settings (
  owner TEXT PRIMARY KEY, include_learned INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
