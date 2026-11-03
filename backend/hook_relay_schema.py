"""The tables behind the Tico Gateway relay client (backend/hook_relay.py); hubdb.MIGRATIONS applies them."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS trigger_relays (
  relay_id TEXT PRIMARY KEY, trigger_id TEXT NOT NULL, address_prefix TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT, hq_gone INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS trigger_relays_live ON trigger_relays(trigger_id) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS hook_relay_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), cursor INTEGER NOT NULL DEFAULT 0,
  acked INTEGER NOT NULL DEFAULT 0, last_pull_at REAL, failing_since REAL,
  last_error TEXT NOT NULL DEFAULT '', stopped_for TEXT NOT NULL DEFAULT '',
  pending INTEGER, oldest TEXT, pending_at REAL);
INSERT OR IGNORE INTO hook_relay_state(singleton) VALUES(1);
"""
