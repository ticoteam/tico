-- The owner's GitHub user token for a personal account (backend/github_app.py, "Personal accounts"): the user access
-- token GitHub issues for the App, and the refresh token that rotates with it. Both are sealed with the at-rest key
-- (AAD b"tico-github-user-token:v1"), never stored in the clear. One row, id 'app', as the App itself; a disconnect
-- forgets it. The expiry times are plain, so a refresh is due without opening the secret. Tico uses the token only to
-- create empty repositories in that account; everything else keeps using installation tokens.
CREATE TABLE IF NOT EXISTS github_user_token(
 id TEXT PRIMARY KEY,
 login TEXT NOT NULL,
 github_id INTEGER NOT NULL DEFAULT 0,
 client_id TEXT NOT NULL DEFAULT '',
 ciphertext BLOB NOT NULL,
 nonce BLOB NOT NULL,
 access_expires_at TEXT,
 refresh_expires_at TEXT,
 authorized_by TEXT NOT NULL,
 authorized_at TEXT NOT NULL,
 refreshed_at TEXT
);

-- Single-use nonces for the owner's authorize link, bound to the session that asked for it (as github_app_states is
-- for the App's setup link). Forgotten with the App.
CREATE TABLE IF NOT EXISTS github_user_states(
 nonce TEXT PRIMARY KEY,
 actor TEXT NOT NULL,
 created_at REAL NOT NULL
);
