"""The tables behind webhook triggers (backend/triggers.py); hubdb.MIGRATIONS applies them."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'webhook',
  secret_hash TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS trigger_deliveries (
  trigger_id TEXT NOT NULL, delivery TEXT NOT NULL, received_at TEXT NOT NULL,
  bytes INTEGER NOT NULL DEFAULT 0, tasks INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(trigger_id, delivery));
CREATE INDEX IF NOT EXISTS trigger_deliveries_at ON trigger_deliveries(trigger_id, received_at);
"""

# A per-trigger signing secret (sealed with the credential vault) and a delivery log that keeps
# refusals beside the accepted calls: `outcome` is accepted or refused, `reason` why it was refused,
# `sent_id` the sender's own delivery id. A refused row's `delivery` is a fresh id, so a refusal never
# blocks the sender's next, correctly signed try with the same id.
SIGNING_SCHEMA = """
ALTER TABLE triggers ADD COLUMN signing_secret TEXT;
ALTER TABLE trigger_deliveries ADD COLUMN outcome TEXT NOT NULL DEFAULT 'accepted';
ALTER TABLE trigger_deliveries ADD COLUMN reason TEXT NOT NULL DEFAULT '';
ALTER TABLE trigger_deliveries ADD COLUMN sent_id TEXT NOT NULL DEFAULT '';
"""
