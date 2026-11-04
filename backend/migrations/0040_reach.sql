-- Reach (backend/reach.py, docs/reach.md): who each teammate may reach. A setting's NULL field takes the value from above.
CREATE TABLE IF NOT EXISTS reach_settings(id TEXT PRIMARY KEY, party_id TEXT NOT NULL,
 mode TEXT CHECK (mode IS NULL OR mode IN ('open','closed')), chats_per_day INTEGER, tasks_per_day INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by_id TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS reach_settings_party ON reach_settings(party_id);
CREATE TABLE IF NOT EXISTS reach_lines(id TEXT PRIMARY KEY, sender_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
 per_day INTEGER, org_rule_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 created_by_id TEXT NOT NULL, updated_by_id TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS reach_lines_pair ON reach_lines(sender_id, receiver_id);
CREATE INDEX IF NOT EXISTS reach_lines_receiver ON reach_lines(receiver_id);
