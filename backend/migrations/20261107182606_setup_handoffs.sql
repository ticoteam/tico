-- Setup handoff (backend/setup_handoff.py): the installer gives a hosted server the sha256 of a one-time secret
-- (TICO_SETUP_HANDOFF_HASH), and the owner's coding agent trades the secret for a personal token once, before the owner
-- has signed in on the web. Like owner_links, only the hash is stored. human_token_id names the token the secret was
-- traded for, so turning the handoff off can also revoke it. IF NOT EXISTS: a server that placed the table on first
-- start keeps its rows.
CREATE TABLE IF NOT EXISTS setup_handoffs (
 token_hash TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 used_at TEXT,
 revoked_at TEXT,
 human_token_id TEXT
);
