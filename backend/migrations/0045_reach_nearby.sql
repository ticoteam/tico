-- Reach gains `nearby` (backend/reach.py, docs/reach.md): a bot reaches its manager, its reports, its peers and its
-- team. SQLite cannot change a CHECK in place, so reach_settings is rebuilt with the third mode; its rows carry over.
CREATE TABLE reach_settings_new(id TEXT PRIMARY KEY, party_id TEXT NOT NULL,
 mode TEXT CHECK (mode IS NULL OR mode IN ('open','nearby','closed')), chats_per_day INTEGER, tasks_per_day INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by_id TEXT NOT NULL);
INSERT INTO reach_settings_new(id, party_id, mode, chats_per_day, tasks_per_day, created_at, updated_at, updated_by_id)
 SELECT id, party_id, mode, chats_per_day, tasks_per_day, created_at, updated_at, updated_by_id FROM reach_settings;
DROP TABLE reach_settings;
ALTER TABLE reach_settings_new RENAME TO reach_settings;
CREATE UNIQUE INDEX IF NOT EXISTS reach_settings_party ON reach_settings(party_id);
