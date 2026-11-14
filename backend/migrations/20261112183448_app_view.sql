-- A bot's read-only view of the app (backend/app_view.py).
-- `bot_app_view`: one row per bot an admin let view the app, read only; no row is off.
-- `app_views`: each view a bot opened, bound to the run that opened it, with its token's digest. Kept as the record of
-- who looked when; the audit log has a line for each as well.
CREATE TABLE IF NOT EXISTS bot_app_view(
 bot TEXT PRIMARY KEY, turned_on_by TEXT NOT NULL, turned_on_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_views(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL, attempt_id TEXT NOT NULL, opened_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS app_views_bot ON app_views(bot, opened_at);
