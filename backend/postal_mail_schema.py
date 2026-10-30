"""Physical mail (backend/postal_mail.py): the letters that reached this company and where the email-in copies come
from. hubdb.MIGRATIONS applies it."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_items (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
  address TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL,
  num_pages INTEGER, file_id TEXT, blob_id TEXT, kind TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  sender TEXT NOT NULL DEFAULT '', deadline TEXT, status TEXT NOT NULL DEFAULT 'new',
  task_id TEXT, delivery TEXT NOT NULL DEFAULT '', raw_json TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT, error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(provider, external_id));
CREATE INDEX IF NOT EXISTS mail_items_received ON mail_items(received_at);
CREATE INDEX IF NOT EXISTS mail_items_due ON mail_items(status, next_try);
CREATE TABLE IF NOT EXISTS mail_intake (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), inbox_id TEXT NOT NULL, set_by TEXT NOT NULL, set_at TEXT NOT NULL);
"""
