"""The tables behind outgoing webhooks (backend/webhooks.py, backend/webhook_delivery.py); hubdb.MIGRATIONS applies them."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS webhooks (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, host TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, events TEXT NOT NULL DEFAULT '[]',
  secret TEXT NOT NULL DEFAULT '', secret_prev TEXT NOT NULL DEFAULT '', secret_prev_until TEXT,
  rate_per_hour INTEGER NOT NULL DEFAULT 600, allow_http INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL, requested_by TEXT NOT NULL DEFAULT '', paused_reason TEXT NOT NULL DEFAULT '',
  notified_at TEXT, notify_task TEXT, window_start TEXT, window_count INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS webhooks_live ON webhooks(created_by) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS webhook_events (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
  payload TEXT NOT NULL DEFAULT '{}', actor TEXT NOT NULL DEFAULT '', skipped INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS webhook_events_created ON webhook_events(created_at);

CREATE TABLE IF NOT EXISTS webhook_sends (
  id TEXT PRIMARY KEY, event_id TEXT NOT NULL, webhook_id TEXT NOT NULL, type TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready',
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, claimed_at TEXT,
  last_status INTEGER, last_error TEXT NOT NULL DEFAULT '', delivered_at TEXT, created_at TEXT NOT NULL,
  UNIQUE(webhook_id, event_id));
CREATE INDEX IF NOT EXISTS webhook_sends_due ON webhook_sends(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS webhook_sends_endpoint ON webhook_sends(webhook_id, created_at);

CREATE TABLE IF NOT EXISTS webhook_attempts (
  send_id TEXT NOT NULL, webhook_id TEXT NOT NULL, n INTEGER NOT NULL, created_at TEXT NOT NULL,
  timestamp_sent INTEGER NOT NULL, status INTEGER, ms INTEGER NOT NULL DEFAULT 0,
  response_head TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(send_id, n));
CREATE INDEX IF NOT EXISTS webhook_attempts_endpoint ON webhook_attempts(webhook_id, created_at);
"""

# An endpoint is a person's: `created_by` is always a person, and `requested_by` the bot whose request a person
# approved. `secret` is sealed with the credential vault, since signing needs the key itself. `host` is the endpoint's
# host name, kept for the per-host limit on sends in flight. A send keeps the exact body it signs, so every attempt of one
# send carries the same bytes under the same `webhook-id`.
