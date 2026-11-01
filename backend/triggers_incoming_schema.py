"""Stored deliveries, the dedupe ledger and per-trigger settings (backend/triggers.py); hubdb.MIGRATIONS applies them.

`trigger_bodies` keeps what a call sent (raw body, headers) until `expires_at`, for reading and replay. `trigger_seen`
is the dedupe ledger: one row per accepted delivery id, kept 30 days, apart from both the bodies and the 200-row
display log, so a late retry is still a duplicate. A trigger's `preset` says how its calls are verified (`hmac` for
every trigger that already had a signing secret); `hourly` NULL means the default cap; `unheard` is `log` or `note`.
"""
SCHEMA = """
ALTER TABLE triggers ADD COLUMN preset TEXT NOT NULL DEFAULT 'none';
ALTER TABLE triggers ADD COLUMN type_from TEXT NOT NULL DEFAULT '';
ALTER TABLE triggers ADD COLUMN hourly INTEGER;
ALTER TABLE triggers ADD COLUMN keep_days INTEGER NOT NULL DEFAULT 7;
ALTER TABLE triggers ADD COLUMN unheard TEXT NOT NULL DEFAULT 'log';
ALTER TABLE triggers ADD COLUMN unheard_noted TEXT;
UPDATE triggers SET preset='hmac' WHERE signing_secret IS NOT NULL;
ALTER TABLE trigger_deliveries ADD COLUMN type TEXT NOT NULL DEFAULT '';
ALTER TABLE trigger_deliveries ADD COLUMN via TEXT NOT NULL DEFAULT 'direct';
ALTER TABLE trigger_deliveries ADD COLUMN rules INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS trigger_bodies (
  trigger_id TEXT NOT NULL, delivery TEXT NOT NULL, received_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT '', via TEXT NOT NULL DEFAULT 'direct',
  headers TEXT NOT NULL DEFAULT '{}', body BLOB NOT NULL,
  PRIMARY KEY(trigger_id, delivery));
CREATE INDEX IF NOT EXISTS trigger_bodies_expires ON trigger_bodies(expires_at);
CREATE TABLE IF NOT EXISTS trigger_seen (
  trigger_id TEXT NOT NULL, delivery TEXT NOT NULL, seen_at TEXT NOT NULL,
  PRIMARY KEY(trigger_id, delivery));
CREATE INDEX IF NOT EXISTS trigger_seen_at ON trigger_seen(seen_at);
INSERT OR IGNORE INTO trigger_seen(trigger_id, delivery, seen_at)
  SELECT trigger_id, delivery, received_at FROM trigger_deliveries WHERE outcome='accepted';
"""

# Who consumes a trigger's calls (spec §3.4): `finance` calls go to Finance (finance/actions.receive), never to rules or
# automations. Mercury triggers made before this column are Finance's already, so they are marked here.
CONSUMER_SCHEMA = """
ALTER TABLE triggers ADD COLUMN consumer TEXT NOT NULL DEFAULT 'automations';
UPDATE triggers SET consumer='finance' WHERE preset='mercury';
"""
