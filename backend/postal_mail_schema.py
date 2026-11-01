"""Physical mail (backend/postal_mail.py): the letters that reached this company and where the email-in copies come
from. hubdb.MIGRATIONS applies it."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_items (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
  address TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL,
  num_pages INTEGER, contains_checks INTEGER NOT NULL DEFAULT 0, file_id TEXT, blob_id TEXT, kind TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  sender TEXT NOT NULL DEFAULT '', deadline TEXT, status TEXT NOT NULL DEFAULT 'new',
  task_id TEXT, delivery TEXT NOT NULL DEFAULT '', raw TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0, next_try_at TEXT, error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(provider, external_id));
CREATE INDEX IF NOT EXISTS mail_items_received ON mail_items(received_at);
CREATE INDEX IF NOT EXISTS mail_items_due ON mail_items(status, next_try_at);
CREATE TABLE IF NOT EXISTS mail_intake (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), inbox_id TEXT NOT NULL, set_by TEXT NOT NULL, set_at TEXT NOT NULL);
"""

# Appended after SCHEMA shipped to main: the scan lookup on every file download (`scan_access`) gets its index, a read
# is stamped for the daily cap (`READS_PER_DAY`), and the email-in address names who may send it scans.
V2 = """
CREATE INDEX IF NOT EXISTS mail_items_blob ON mail_items(blob_id);
ALTER TABLE mail_items ADD COLUMN read_at TEXT;
CREATE INDEX IF NOT EXISTS mail_items_read ON mail_items(read_at);
ALTER TABLE mail_intake ADD COLUMN senders TEXT NOT NULL DEFAULT '[]';
"""

# Appended after V2: bill, financial and tax letters filed before V2 made their tasks private are listed once here, and
# PostalMail.backfill_private makes those tasks private at the next start (hubdb._made_private needs Python). Every read
# also gets its own row, so a retried letter counts against the daily cap each time it is read; the last read of each
# letter carries over, so an upgrade does not restart the day's count.
V3 = """
CREATE TABLE IF NOT EXISTS mail_private_backfill (task_id TEXT PRIMARY KEY);
INSERT OR IGNORE INTO mail_private_backfill SELECT task_id FROM mail_items
  WHERE kind IN ('government_tax','bill','financial') AND task_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS mail_reads (item_id TEXT NOT NULL, at TEXT NOT NULL);
INSERT INTO mail_reads SELECT id, read_at FROM mail_items WHERE read_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM mail_reads);
CREATE INDEX IF NOT EXISTS mail_reads_at ON mail_reads(at);
"""
