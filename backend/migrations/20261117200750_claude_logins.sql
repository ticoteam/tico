-- A Claude plan signed in with a full login (`claude_login`, backend/claude_login.py), one row per plan beside its
-- `model_subscriptions` row (a table of its own, so this file and others that change model_subscriptions apply in any
-- order): the account it is (lower(email)|org uuid, one plan per account), when its access token and its login run
-- out, when a computer last refreshed it, and the lease by which one computer at a time refreshes it (which one, from
-- which revision, until when, and the last lease a stored result came from, so a lost answer is acknowledged again).
CREATE TABLE IF NOT EXISTS claude_logins(
  subscription_id TEXT PRIMARY KEY,
  account TEXT NOT NULL DEFAULT '',
  access_expires_at TEXT,
  login_expires_at TEXT,
  refreshed_at TEXT,
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
CREATE UNIQUE INDEX IF NOT EXISTS claude_logins_account ON claude_logins(account) WHERE account<>'';
-- Every lease handed out: a refresh's result, late or repeated, counts only from the computer its lease went to.
CREATE TABLE IF NOT EXISTS claude_login_leases(
  id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,
  runner_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  issued_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claude_login_leases_issued ON claude_login_leases(issued_at);
-- A queued turn refused on a full login while its refresh was under way waits for the new token, a few minutes at most:
-- the plan it was refused on, the revision it held, and until when it waits (backend/claude_login.py `gap`).
CREATE TABLE IF NOT EXISTS claude_login_parks(
  job_id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL,
  subscription_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  until TEXT NOT NULL,
  created_at TEXT NOT NULL
);
