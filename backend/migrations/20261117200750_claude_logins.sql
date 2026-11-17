-- A Claude plan signed in with a full login (`claude_login`, backend/claude_login.py): the account it is (lower(email)|org
-- uuid, one plan per account), when its access token and its login run out, and when a computer last refreshed it.
ALTER TABLE model_subscriptions ADD COLUMN account TEXT NOT NULL DEFAULT '';
ALTER TABLE model_subscriptions ADD COLUMN access_expires_at TEXT;
ALTER TABLE model_subscriptions ADD COLUMN login_expires_at TEXT;
ALTER TABLE model_subscriptions ADD COLUMN refreshed_at TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS model_subscriptions_account ON model_subscriptions(account) WHERE account<>'';
-- One computer at a time refreshes a plan's login (a lease): which one, from which revision, until when, and the last
-- lease a stored result came from (so a lost answer is acknowledged again).
CREATE TABLE IF NOT EXISTS claude_logins(
  subscription_id TEXT PRIMARY KEY,
  lease_id TEXT NOT NULL DEFAULT '',
  lease_runner TEXT NOT NULL DEFAULT '',
  lease_revision INTEGER NOT NULL DEFAULT 0,
  lease_until TEXT,
  failures INTEGER NOT NULL DEFAULT 0,
  retry_at TEXT,
  done_lease TEXT NOT NULL DEFAULT '',
  done_revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
