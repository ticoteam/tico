-- GitHub installation tokens the server minted for bots (backend/github_app.py keep_tokens), kept until they expire so
-- a restarted server hands out the same token instead of asking GitHub for a new one for every bot. `scope` is the
-- token's repositories and permissions as JSON (no secret); the token itself is sealed (AES-GCM, `nonce` and
-- `ciphertext`) with the key that seals the GitHub App's own secrets, never stored in the clear. `expires_at` is
-- GitHub's. Connecting or forgetting the App, and a repository refresh, delete every row.
CREATE TABLE IF NOT EXISTS github_installation_tokens (
  scope TEXT PRIMARY KEY,
  expires_at TEXT NOT NULL,
  nonce BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  created_at TEXT NOT NULL
);
