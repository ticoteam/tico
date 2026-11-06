-- Existing letters and routing stay unchanged until an owner or admin opts in.
CREATE TABLE IF NOT EXISTS postal_mail_grants (
  id TEXT PRIMARY KEY,
  address_id TEXT NOT NULL,
  grantee_type TEXT NOT NULL CHECK (grantee_type IN ('person','bot')),
  grantee_id TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'read' CHECK (level='read'),
  granted_by_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(address_id,grantee_type,grantee_id)
);
CREATE INDEX IF NOT EXISTS postal_mail_grants_grantee ON postal_mail_grants(grantee_type,grantee_id,address_id);
CREATE TABLE IF NOT EXISTS postal_mail_settings (
  id INTEGER PRIMARY KEY CHECK (id=1),
  legal_bot_id TEXT,
  updated_by_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO postal_mail_settings(id,legal_bot_id,updated_by_id,created_at,updated_at)
VALUES(1,NULL,NULL,strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
