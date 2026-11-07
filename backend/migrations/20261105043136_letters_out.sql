CREATE TABLE IF NOT EXISTS letter_outboxes (
  id TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'ask_first', return_address TEXT,
  monthly_cap_micros INTEGER, availability TEXT NOT NULL DEFAULT 'unknown',
  checked_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO letter_outboxes(id,created_at,updated_at) VALUES('company',strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'));
CREATE TABLE IF NOT EXISTS letter_outbox_grants (
  id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES letter_outboxes(id), grantee_type TEXT NOT NULL,
  grantee_id TEXT NOT NULL, level TEXT NOT NULL CHECK(level='send'),
  granted_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(outbox_id,grantee_type,grantee_id)
);
CREATE TABLE IF NOT EXISTS letter_bot_settings (
  bot_id TEXT PRIMARY KEY REFERENCES bots(id), mode TEXT, monthly_cap_micros INTEGER,
  updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS letters (
  id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES letter_outboxes(id),
  requested_by TEXT NOT NULL, acting_bot_id TEXT, automation_id TEXT, task_id TEXT, thread_id TEXT, attempt_id TEXT,
  recipient TEXT NOT NULL, recipient_hash TEXT NOT NULL, return_address TEXT NOT NULL,
  mail_class TEXT NOT NULL, color INTEGER NOT NULL, duplex INTEGER NOT NULL,
  source_blob_id TEXT NOT NULL REFERENCES blobs(id), original_blob_id TEXT REFERENCES blobs(id), proof_blob_id TEXT REFERENCES blobs(id),
  source_provenance TEXT NOT NULL DEFAULT '{}',
  page_count INTEGER, quote_id TEXT, quote_expires_at TEXT, approval_hash TEXT,
  approval_id TEXT, approved_hash TEXT, submission_quote_id TEXT, hq_letter_id TEXT,
  total_micros INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'quoted', tracking TEXT NOT NULL DEFAULT '[]',
  cancel_possible INTEGER NOT NULL DEFAULT 0, always_ask INTEGER NOT NULL DEFAULT 1, approval_reason TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, next_sync_at TEXT,
  submitted_at TEXT, accepted_at TEXT, charged_micros INTEGER, spend_released INTEGER NOT NULL DEFAULT 0,
  billing TEXT NOT NULL DEFAULT 'unknown', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS letters_due ON letters(status,next_sync_at);
CREATE INDEX IF NOT EXISTS letters_spend ON letters(submitted_at,acting_bot_id,status);
CREATE INDEX IF NOT EXISTS letters_recipient ON letters(recipient_hash,status);
CREATE INDEX IF NOT EXISTS letters_approval ON letters(approval_id);
CREATE INDEX IF NOT EXISTS letters_source ON letters(source_blob_id);
CREATE TABLE IF NOT EXISTS letter_proofs (
  letter_id TEXT NOT NULL REFERENCES letters(id), blob_id TEXT NOT NULL REFERENCES blobs(id),
  approval_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(letter_id,blob_id)
);
CREATE INDEX IF NOT EXISTS letter_proofs_blob ON letter_proofs(blob_id);
